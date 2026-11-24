"""Reservation expiry, cancellation, and waitlist promotion services."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from functools import partial
from time import sleep

from django.db import OperationalError, transaction
from django.utils import timezone

from accounts.models import Customer
from activitylog.services import ActivityAction, log_activity
from inventory.models import InventoryItem
from inventory.services import lock_item, release
from invoicing.models import Invoice
from mining.models import BackupMiner, Reservation
from mining.services import (
    ReservationConflict,
    ReservationNotFound,
    ReservationValidationError,
    _recompute_order_total,
    create_reservation_for_comment,
    enqueue_invoice_after_commit,
)
from orders.models import Order, OrderItem

logger = logging.getLogger(__name__)

_LIVE_RESERVATION_STATUSES = (Reservation.Status.ACTIVE, Reservation.Status.CONFIRMED)
_MAX_DEADLOCK_RETRIES = 3


def _is_postgres_deadlock(error: OperationalError) -> bool:
    cause = error.__cause__
    return getattr(cause, "sqlstate", None) == "40P01"


def _retry_deadlock[T](operation: Callable[[], T], *, description: str) -> T:
    for attempt in range(_MAX_DEADLOCK_RETRIES + 1):
        try:
            return operation()
        except OperationalError as error:
            if not _is_postgres_deadlock(error) or attempt == _MAX_DEADLOCK_RETRIES:
                raise
            logger.warning(
                "Retrying %s after PostgreSQL deadlock (attempt %s/%s).",
                description,
                attempt + 1,
                _MAX_DEADLOCK_RETRIES,
            )
            sleep(0.05 * (attempt + 1))
    raise RuntimeError("Deadlock retry loop ended unexpectedly.")


def _lock_item_reservations(item_id: int) -> list[Reservation]:
    """Lock all item reservations in ascending primary-key order."""
    return list(
        Reservation.objects.select_for_update().filter(item_id=item_id).order_by("pk")
    )


def _lock_customers(customer_ids: set[int]) -> dict[int, Customer]:
    """Lock customer rows consistently before their order rows."""
    return {
        customer.pk: customer
        for customer in Customer.objects.select_for_update()
        .filter(pk__in=customer_ids)
        .order_by("pk")
    }


def _order_ids_for(customer_id: int, item_id: int) -> set[int]:
    """Find order ids associated with the customer and item without locking."""
    return set(
        OrderItem.objects.filter(order__customer_id=customer_id, item_id=item_id)
        .order_by("order_id")
        .values_list("order_id", flat=True)
    )


def _lock_orders(order_ids: set[int]) -> dict[int, Order]:
    """Lock order rows in ascending primary-key order."""
    return {
        order.pk: order
        for order in Order.objects.select_for_update()
        .filter(pk__in=order_ids)
        .order_by("pk")
    }


def _choose_order(orders: list[Order]) -> Order | None:
    """Prefer the current unpaid order, otherwise return the latest historical one."""
    unpaid = [order for order in orders if order.status == Order.Status.UNPAID]
    return unpaid[0] if unpaid else (orders[-1] if orders else None)


def _remove_order_quantity(
    order: Order | None,
    item: InventoryItem,
    qty: int,
    *,
    actor,
) -> None:
    """Remove a reservation's exact quantity from its order line."""
    if order is None:
        return
    line = OrderItem.objects.select_for_update().filter(order=order, item=item).first()
    if line is None or line.quantity < qty:
        raise ReservationConflict("The order line does not match the reservation.")
    previous = {"quantity": line.quantity, "unit_price": line.unit_price}
    remaining = line.quantity - qty
    line_id = line.pk
    if remaining:
        line.quantity = remaining
        line.save(update_fields=["quantity"])
    else:
        line.delete()
    log_activity(
        user=actor,
        action=ActivityAction.ORDER_ITEM_UPDATED,
        target_type="OrderItem",
        target_id=line_id,
        previous_value=previous,
        new_value={
            "order_id": order.pk,
            "item_id": item.pk,
            "quantity": remaining,
            "unit_price": previous["unit_price"],
        },
        reason="Reservation released.",
    )
    _recompute_order_total(order, actor=actor)


def _set_invoice_expired_if_empty(order: Order | None, *, actor) -> None:
    """Expire a sent invoice when reservation cleanup removes the last line."""
    if order is None or order.items.exists():
        return
    invoice = Invoice.objects.select_for_update().filter(order=order).first()
    if invoice is None or invoice.status != Invoice.Status.SENT:
        return
    previous_status = invoice.status
    invoice.status = Invoice.Status.EXPIRED
    invoice.save(update_fields=["status"])
    log_activity(
        user=actor,
        action=ActivityAction.INVOICE_UPDATED,
        target_type="Invoice",
        target_id=invoice.pk,
        previous_value={"status": previous_status},
        new_value={"status": invoice.status},
        reason="Order has no remaining items.",
    )


def _log_backup_status(backup: BackupMiner, *, status: str, actor, reason: str) -> None:
    """Persist and audit a waitlist status transition."""
    previous = backup.status
    backup.status = status
    backup.save(update_fields=["status"])
    log_activity(
        user=actor,
        action=(
            ActivityAction.RESERVATION_BACKUP_PROMOTED
            if status == BackupMiner.Status.PROMOTED
            else ActivityAction.RESERVATION_BACKUP_DROPPED
        ),
        target_type="BackupMiner",
        target_id=backup.pk,
        previous_value={"status": previous},
        new_value={"status": status, "comment_id": backup.comment_id},
        reason=reason,
    )


def _close_reservation_locked(
    item: InventoryItem,
    reservation: Reservation,
    *,
    new_status: str,
    actor,
) -> bool:
    """Release one reservation and promote at most one eligible backup."""
    active_reservations = [
        row
        for row in Reservation.objects.filter(
            item_id=item.pk,
            status__in=_LIVE_RESERVATION_STATUSES,
        ).order_by("pk")
    ]
    other_holders = [
        row
        for row in active_reservations
        if row.pk != reservation.pk and row.status in _LIVE_RESERVATION_STATUSES
    ]

    waiting = list(
        BackupMiner.objects.select_for_update()
        .filter(item=item, status=BackupMiner.Status.WAITING)
        .select_related("customer", "comment")
        .order_by("comment__created_time", "comment__fb_comment_id")
    )
    prospective_available = item.stock_qty - item.reserved_qty + reservation.qty
    skipped: list[tuple[BackupMiner, str]] = []
    chosen: BackupMiner | None = None
    for backup in waiting:
        if any(holder.customer_id == backup.customer_id for holder in other_holders):
            skipped.append((backup, "Customer already holds this item."))
        elif prospective_available <= 0:
            skipped.append((backup, "Item has no available stock after release."))
        else:
            chosen = backup
            break

    customer_ids = {reservation.customer_id}
    if chosen is not None:
        customer_ids.add(chosen.customer_id)
    locked_customers = _lock_customers(customer_ids)
    order_ids = _order_ids_for(reservation.customer_id, item.pk)
    chosen_order_ids: set[int] = set()
    if chosen is not None:
        chosen_order_ids = set(
            Order.objects.filter(
                customer_id=chosen.customer_id,
                status=Order.Status.UNPAID,
            ).values_list("pk", flat=True)
        )
    locked_orders = _lock_orders(order_ids | chosen_order_ids)
    old_orders = [
        locked_orders[order_id]
        for order_id in sorted(order_ids)
        if order_id in locked_orders
    ]
    old_order = _choose_order(old_orders)
    if old_order is not None and old_order.status in {
        Order.Status.PAID,
        Order.Status.PACKAGED,
    }:
        return False

    for backup, reason in skipped:
        _log_backup_status(
            backup,
            status=BackupMiner.Status.DROPPED,
            actor=actor,
            reason=reason,
        )

    old_status = reservation.status
    reservation.status = new_status
    reservation.save(update_fields=["status"])
    item = release(item, reservation.qty, actor=actor)
    _remove_order_quantity(old_order, item, reservation.qty, actor=actor)
    log_activity(
        user=actor,
        action=(
            ActivityAction.RESERVATION_EXPIRED
            if new_status == Reservation.Status.EXPIRED
            else ActivityAction.RESERVATION_CANCELLED
        ),
        target_type="Reservation",
        target_id=reservation.pk,
        previous_value={"status": old_status},
        new_value={"status": reservation.status, "qty": reservation.qty},
    )

    promoted_order = None
    if chosen is not None:
        live_customer_ids = {
            holder.customer_id
            for holder in other_holders
            if holder.status in _LIVE_RESERVATION_STATUSES
        }
        if chosen.customer_id in live_customer_ids:
            _log_backup_status(
                chosen,
                status=BackupMiner.Status.DROPPED,
                actor=actor,
                reason="Customer already holds this item.",
            )
        elif item.stock_qty - item.reserved_qty > 0:
            promoted = create_reservation_for_comment(
                item,
                locked_customers[chosen.customer_id],
                chosen.comment,
                actor=actor,
                qty=1,
                preserve_comment_outcome=True,
                enqueue_invoice=False,
            )
            _log_backup_status(
                chosen,
                status=BackupMiner.Status.PROMOTED,
                actor=actor,
                reason="Reservation expired or was cancelled.",
            )
            promoted_order = Order.objects.get(
                customer_id=chosen.customer_id,
                status=Order.Status.UNPAID,
            )
            log_activity(
                user=actor,
                action=ActivityAction.RESERVATION_BACKUP_PROMOTED,
                target_type="Reservation",
                target_id=promoted.pk,
                new_value={
                    "reservation_id": promoted.pk,
                    "backup_id": chosen.pk,
                    "customer_id": chosen.customer_id,
                    "payment_deadline": promoted.payment_deadline,
                },
            )

    if old_order is not None:
        if old_order.items.exists():
            transaction.on_commit(
                partial(
                    enqueue_invoice_after_commit,
                    old_order.pk,
                    reason="reservation expired or cancelled",
                    caller="_close_reservation_locked",
                )
            )
        else:
            _set_invoice_expired_if_empty(old_order, actor=actor)
    if promoted_order is not None:
        transaction.on_commit(
            partial(
                enqueue_invoice_after_commit,
                promoted_order.pk,
                reason="backup promoted",
                caller="_close_reservation_locked",
            )
        )
    return True


def expire_reservation(reservation_id: int, *, now: datetime | None = None) -> bool:
    """Expire a due ACTIVE reservation under item-then-reservation lock order."""
    now = now or timezone.now()
    return _retry_deadlock(
        lambda: _expire_reservation_once(reservation_id, now=now),
        description=f"expiry for reservation {reservation_id}",
    )


def _expire_reservation_once(reservation_id: int, *, now: datetime) -> bool:
    """Run one transactional expiry attempt."""
    try:
        item_id = Reservation.objects.values_list("item_id", flat=True).get(
            pk=reservation_id
        )
    except Reservation.DoesNotExist:
        return False

    with transaction.atomic():
        item = lock_item(InventoryItem.objects.get(pk=item_id))
        locked = _lock_item_reservations(item.pk)
        reservation = next((row for row in locked if row.pk == reservation_id), None)
        if (
            reservation is None
            or reservation.status != Reservation.Status.ACTIVE
            or reservation.payment_deadline > now
        ):
            return False
        return _close_reservation_locked(
            item,
            reservation,
            new_status=Reservation.Status.EXPIRED,
            actor=None,
        )


def expire_due_reservations(*, now: datetime | None = None) -> dict[str, int]:
    """Process each due reservation independently, logging and continuing errors."""
    now = now or timezone.now()
    candidate_ids = list(
        Reservation.objects.filter(
            status=Reservation.Status.ACTIVE,
            payment_deadline__lte=now,
        )
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    expired = 0
    errors = 0
    for reservation_id in candidate_ids:
        try:
            expired += expire_reservation(reservation_id, now=now)
        except Exception:
            errors += 1
            logger.exception("Could not expire reservation %s.", reservation_id)
    return {"candidates": len(candidate_ids), "expired": expired, "errors": errors}


def manage_reservation(
    reservation_id: int,
    *,
    changes: dict[str, object],
    actor,
    now: datetime | None = None,
) -> Reservation:
    """Extend a deadline, confirm, or cancel through the shared close path."""
    allowed = {"payment_deadline", "status"}
    unknown = changes.keys() - allowed
    if unknown:
        raise ReservationValidationError(
            f"Unsupported reservation fields: {', '.join(sorted(unknown))}."
        )
    if not changes:
        raise ReservationValidationError("At least one change is required.")
    try:
        item_id = Reservation.objects.values_list("item_id", flat=True).get(
            pk=reservation_id
        )
    except Reservation.DoesNotExist as exc:
        raise ReservationNotFound("Reservation not found.") from exc

    now = now or timezone.now()
    with transaction.atomic():
        item = lock_item(InventoryItem.objects.get(pk=item_id))
        locked = _lock_item_reservations(item.pk)
        reservation = next((row for row in locked if row.pk == reservation_id), None)
        if reservation is None:
            raise ReservationNotFound("Reservation not found.")

        if "payment_deadline" in changes:
            deadline = changes["payment_deadline"]
            if not isinstance(deadline, datetime) or deadline <= now:
                raise ReservationValidationError(
                    "payment_deadline must be a future datetime."
                )
            if deadline <= reservation.payment_deadline:
                raise ReservationValidationError(
                    "payment_deadline must extend the current deadline."
                )
            if reservation.status != Reservation.Status.ACTIVE:
                raise ReservationConflict("Only active reservations can be extended.")
            previous_deadline = reservation.payment_deadline
            reservation.payment_deadline = deadline
            reservation.save(update_fields=["payment_deadline"])
            log_activity(
                user=actor,
                action=ActivityAction.RESERVATION_UPDATED,
                target_type="Reservation",
                target_id=reservation.pk,
                previous_value={"payment_deadline": previous_deadline},
                new_value={"payment_deadline": deadline},
                reason="Payment deadline extended.",
            )

        desired_status = changes.get("status")
        if desired_status is None:
            return reservation
        if desired_status == Reservation.Status.CONFIRMED:
            if reservation.status != Reservation.Status.ACTIVE:
                raise ReservationConflict("Only active reservations can be confirmed.")
            previous_status = reservation.status
            reservation.status = Reservation.Status.CONFIRMED
            reservation.save(update_fields=["status"])
            log_activity(
                user=actor,
                action=ActivityAction.RESERVATION_CONFIRMED,
                target_type="Reservation",
                target_id=reservation.pk,
                previous_value={"status": previous_status},
                new_value={"status": reservation.status},
            )
            return reservation
        if desired_status == Reservation.Status.CANCELLED:
            if reservation.status not in _LIVE_RESERVATION_STATUSES:
                raise ReservationConflict("This reservation cannot be cancelled.")
            if _close_reservation_locked(
                item,
                reservation,
                new_status=Reservation.Status.CANCELLED,
                actor=actor,
            ):
                return reservation
            raise ReservationConflict("A paid or packaged order cannot be cancelled.")
        raise ReservationValidationError("status must be CONFIRMED or CANCELLED.")
