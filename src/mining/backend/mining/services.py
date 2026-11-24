"""Mining comment parsing and first-claim reservation services."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import partial
from typing import TYPE_CHECKING, TypedDict

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from accounts.models import Customer
from activitylog.services import ActivityAction, log_activity
from inventory.models import InventoryItem
from inventory.services import find_item, lock_item, reserve
from invoicing.models import Invoice
from invoicing.tasks import send_invoice
from mining.models import BackupMiner, LiveComment, Reservation
from orders.models import Order, OrderItem

logger = logging.getLogger("invoicing")

if TYPE_CHECKING:
    from accounts.models import User


class CommentData(TypedDict):
    """Normalized comment fields supplied by any comment source."""

    fb_comment_id: str
    live_video_id: str
    message: str
    fb_commenter_id: str
    fb_commenter_name: str
    created_time: datetime


@dataclass(frozen=True)
class BackupPosition:
    """Result for a valid mine waiting behind the current reservation."""

    comment: LiveComment
    outcome: str
    priority: int


@dataclass(frozen=True)
class InvalidMine:
    """Result for an ignored, invalid, or late-arriving mine."""

    comment: LiveComment
    outcome: str


class ReservationServiceError(Exception):
    """Base error for reservation-management service operations."""


class ReservationConflict(ReservationServiceError):
    """Raised when a requested reservation transition is not allowed."""


class ReservationValidationError(ReservationServiceError):
    """Raised when reservation-management input is invalid."""


class ReservationNotFound(ReservationServiceError):
    """Raised when a requested comment or reservation does not exist."""


def normalize_message(message: str) -> str:
    """Lowercase a message and collapse all whitespace runs."""
    return " ".join(message.lower().split())


def parse_code(message: str) -> str | None:
    """Extract one complete mining code using the configured full-message regex."""
    match = re.fullmatch(settings.MINING_PATTERN, message)
    if match is None:
        return None
    if not match.lastindex:
        raise ValueError("MINING_PATTERN must capture the mining code in group 1.")
    return match.group(1)


def validate_item(code: str) -> InventoryItem | None:
    """Find an item eligible to be mined by its normalized code."""
    return find_item(code)


def get_or_create_customer(
    comment: LiveComment, *, actor: User | None = None
) -> Customer:
    """Resolve the commenter and fill a name only when the stored name is blank."""
    customer, created = Customer.objects.get_or_create(
        facebook_id=comment.fb_commenter_id,
        defaults={"full_name": comment.fb_commenter_name},
    )
    if created:
        log_activity(
            user=actor,
            action=ActivityAction.CUSTOMER_CREATED,
            target_type="Customer",
            target_id=customer.pk,
            new_value={
                "facebook_id": customer.facebook_id,
                "full_name": customer.full_name,
            },
        )
    elif not customer.full_name and comment.fb_commenter_name:
        previous_name = customer.full_name
        customer.full_name = comment.fb_commenter_name
        customer.save(update_fields=["full_name"])
        log_activity(
            user=actor,
            action=ActivityAction.CUSTOMER_UPDATED,
            target_type="Customer",
            target_id=customer.pk,
            previous_value={"full_name": previous_name},
            new_value={"full_name": customer.full_name},
            reason="Filled missing Facebook display name.",
        )
    return customer


def _comment_key(comment: LiveComment) -> tuple[datetime, str]:
    """Return the deterministic original-comment ordering key."""
    return comment.created_time, comment.fb_comment_id


def _record_result(comment: LiveComment) -> Reservation | BackupPosition | InvalidMine:
    """Recreate the result represented by a previously stored comment."""
    if comment.outcome == LiveComment.Outcome.RESERVED:
        if comment.reservation_id is None:
            raise RuntimeError(
                f"Reserved comment {comment.fb_comment_id} has no reservation."
            )
        return Reservation.objects.get(pk=comment.reservation_id)
    if comment.outcome == LiveComment.Outcome.BACKUP:
        backup = BackupMiner.objects.get(comment=comment)
        return BackupPosition(
            comment=comment,
            outcome=comment.outcome,
            priority=backup.priority,
        )
    return InvalidMine(comment=comment, outcome=comment.outcome)


def _log_comment_outcome(
    comment: LiveComment,
    *,
    action: str,
    previous_value: object = None,
    new_value: object = None,
    reason: str = "",
    actor: User | None = None,
) -> None:
    """Write an audit record for a changed comment outcome."""
    log_activity(
        user=actor,
        action=action,
        target_type="LiveComment",
        target_id=comment.pk,
        previous_value=previous_value,
        new_value=new_value,
        reason=reason,
    )


def _set_comment_outcome(
    comment: LiveComment,
    outcome: str,
    *,
    priority: int | None = None,
    reservation: Reservation | None = None,
) -> None:
    """Persist an outcome and any associated queue/reservation fields."""
    comment.outcome = outcome
    comment.priority = priority
    comment.reservation = reservation
    comment.save(update_fields=["outcome", "priority", "reservation"])


def _is_duplicate_mine(item: InventoryItem, customer: Customer) -> bool:
    """Check if the customer already has a live claim on this item."""
    has_reservation = Reservation.objects.filter(
        item=item,
        customer=customer,
        status__in=[Reservation.Status.ACTIVE, Reservation.Status.CONFIRMED],
    ).exists()
    has_backup = BackupMiner.objects.filter(
        item=item,
        customer=customer,
        status=BackupMiner.Status.WAITING,
    ).exists()
    return has_reservation or has_backup


def _later_holder(item: InventoryItem, comment: LiveComment) -> Reservation | None:
    """Find a live holder whose original comment ranks after this comment."""
    holders = (
        Reservation.objects.filter(
            item=item,
            status__in=[Reservation.Status.ACTIVE, Reservation.Status.CONFIRMED],
        )
        .select_related("source_comment")
        .order_by("source_comment__created_time", "source_comment__fb_comment_id")
    )
    incoming_key = _comment_key(comment)
    return next(
        (
            holder
            for holder in holders
            if _comment_key(holder.source_comment) > incoming_key
        ),
        None,
    )


def _unpaid_order(customer: Customer, *, actor: User | None = None) -> Order:
    """Find or create a customer's unpaid order while serializing that customer."""
    locked_customer = Customer.objects.select_for_update().get(pk=customer.pk)
    order = (
        Order.objects.select_for_update()
        .filter(customer=locked_customer, status=Order.Status.UNPAID)
        .first()
    )
    if order is not None:
        return order

    order = Order.objects.create(
        customer=locked_customer,
        status=Order.Status.UNPAID,
        shipping_address=locked_customer.address,
        total_amount=Decimal("0.00"),
    )
    log_activity(
        user=actor,
        action=ActivityAction.ORDER_CREATED,
        target_type="Order",
        target_id=order.pk,
        new_value={"customer_id": customer.pk, "status": order.status},
    )
    return order


def _add_order_item(
    order: Order,
    item: InventoryItem,
    *,
    qty: int = 1,
    unit_price: Decimal | None = None,
    actor: User | None = None,
) -> OrderItem:
    """Add or merge a line while preserving the original agreed price."""
    order_item = (
        OrderItem.objects.select_for_update().filter(order=order, item=item).first()
    )
    if order_item is None:
        order_item = OrderItem.objects.create(
            order=order,
            item=item,
            quantity=qty,
            unit_price=unit_price if unit_price is not None else item.selling_price,
        )
        action = ActivityAction.ORDER_ITEM_ADDED
        previous_value = None
    else:
        if unit_price is not None and order_item.unit_price != unit_price:
            raise ValueError(
                "Cannot merge a transferred item with a different agreed price."
            )
        previous_value = {
            "quantity": order_item.quantity,
            "unit_price": order_item.unit_price,
        }
        order_item.quantity += qty
        order_item.save(update_fields=["quantity"])
        action = ActivityAction.ORDER_ITEM_UPDATED

    log_activity(
        user=actor,
        action=action,
        target_type="OrderItem",
        target_id=order_item.pk,
        previous_value=previous_value,
        new_value={
            "order_id": order.pk,
            "item_id": item.pk,
            "quantity": order_item.quantity,
            "unit_price": order_item.unit_price,
        },
    )
    return order_item


def _recompute_order_total(order: Order, *, actor: User | None = None) -> Decimal:
    """Recalculate the total using exact Decimal multiplication and addition."""
    previous_total = order.total_amount
    total = sum(
        (line.quantity * line.unit_price for line in order.items.all()),
        Decimal("0.00"),
    )
    if total != previous_total:
        order.total_amount = total
        order.save(update_fields=["total_amount"])
        log_activity(
            user=actor,
            action=ActivityAction.ORDER_TOTAL_UPDATED,
            target_type="Order",
            target_id=order.pk,
            previous_value={"total_amount": previous_total},
            new_value={"total_amount": total},
        )
    return total


def enqueue_invoice_after_commit(
    order_id: int,
    *,
    reason: str = "reservation changed",
    caller: str = "enqueue_invoice_after_commit",
) -> None:
    """Enqueue invoice generation after the surrounding database commit."""
    countdown = settings.INVOICE_DELAY_SECONDS
    logger.info(
        "enqueue order_id=%s reason=%s countdown=%s caller=%s",
        order_id,
        reason,
        countdown,
        caller,
    )
    if countdown > 0:
        send_invoice.apply_async(args=[order_id], countdown=countdown)
    else:
        send_invoice.delay(order_id)


def _set_invalid(
    comment: LiveComment,
    *,
    outcome: str,
    action: str,
    reason: str,
    actor: User | None = None,
) -> InvalidMine:
    """Store and log an INVALID or IGNORED comment result."""
    _set_comment_outcome(comment, outcome)
    _log_comment_outcome(
        comment,
        action=action,
        new_value={"outcome": outcome, "parsed_code": comment.parsed_code},
        reason=reason,
        actor=actor,
    )
    return InvalidMine(comment=comment, outcome=outcome)


def add_backup_miner(
    item: InventoryItem,
    customer: Customer,
    comment: LiveComment,
    *,
    comment_outcome: str = LiveComment.Outcome.BACKUP,
    actor: User | None = None,
) -> BackupPosition:
    """Insert a backup and rank it by original order, preserving its outcome."""
    waiting: QuerySet[BackupMiner] = (
        BackupMiner.objects.select_for_update()
        .filter(item=item, status=BackupMiner.Status.WAITING)
        .select_related("comment")
        .order_by("comment__created_time", "comment__fb_comment_id")
    )
    existing = list(waiting)
    position = sum(
        _comment_key(entry.comment) < _comment_key(comment) for entry in existing
    )
    backup = BackupMiner.objects.create(
        item=item,
        customer=customer,
        comment=comment,
        priority=position + 1,
        status=BackupMiner.Status.WAITING,
    )
    log_activity(
        user=actor,
        action=ActivityAction.BACKUP_ADDED,
        target_type="BackupMiner",
        target_id=backup.pk,
        new_value={
            "item_id": item.pk,
            "customer_id": customer.pk,
            "comment_id": comment.pk,
            "priority": backup.priority,
            "status": backup.status,
        },
    )
    ordered = sorted(
        [*existing, backup],
        key=lambda entry: _comment_key(entry.comment),
    )
    for rank, entry in enumerate(ordered, start=1):
        if entry.pk == backup.pk or entry.priority == rank:
            continue
        previous_priority = entry.priority
        entry.priority = rank
        entry.save(update_fields=["priority"])
        log_activity(
            user=actor,
            action=ActivityAction.BACKUP_PRIORITY_UPDATED,
            target_type="BackupMiner",
            target_id=entry.pk,
            previous_value={"priority": previous_priority},
            new_value={"priority": rank},
            reason="Re-ranked by original comment timestamp and id.",
        )

    _set_comment_outcome(
        comment,
        comment_outcome,
        priority=backup.priority,
    )
    _log_comment_outcome(
        comment,
        action=ActivityAction.MINE_BACKUP,
        new_value={"outcome": comment.outcome, "priority": backup.priority},
        actor=actor,
    )
    return BackupPosition(
        comment=comment,
        outcome=comment.outcome,
        priority=backup.priority,
    )


def attempt_reservation(
    item: InventoryItem,
    customer: Customer,
    comment: LiveComment,
    *,
    actor: User | None = None,
) -> Reservation | BackupPosition | InvalidMine:
    """Apply duplicate/late rules, then reserve stock or append a backup."""
    if _is_duplicate_mine(item, customer):
        return _set_invalid(
            comment,
            outcome=LiveComment.Outcome.IGNORED,
            action=ActivityAction.MINE_IGNORED,
            reason=(
                "Customer already has an ACTIVE/CONFIRMED reservation "
                "or WAITING backup."
            ),
            actor=actor,
        )

    later_holder = _later_holder(item, comment)
    if later_holder is not None:
        _set_comment_outcome(comment, LiveComment.Outcome.FLAGGED_LATE)
        _log_comment_outcome(
            comment,
            action=ActivityAction.MINE_FLAGGED_LATE,
            new_value={
                "outcome": comment.outcome,
                "incoming_comment_id": comment.fb_comment_id,
                "holder_comment_id": later_holder.source_comment.fb_comment_id,
            },
            reason="Earlier original comment arrived after a later-ranked holder.",
            actor=actor,
        )
        add_backup_miner(
            item,
            customer,
            comment,
            comment_outcome=LiveComment.Outcome.FLAGGED_LATE,
            actor=actor,
        )
        return InvalidMine(comment=comment, outcome=comment.outcome)

    if item.stock_qty - item.reserved_qty <= 0:
        return add_backup_miner(item, customer, comment, actor=actor)

    return create_reservation_for_comment(
        item,
        customer,
        comment,
        actor=actor,
    )


def create_reservation_for_comment(
    item: InventoryItem,
    customer: Customer,
    comment: LiveComment,
    *,
    actor: User | None = None,
    qty: int = 1,
    unit_price: Decimal | None = None,
    payment_deadline: datetime | None = None,
    stock_already_reserved: bool = False,
    preserve_comment_outcome: bool = False,
    enqueue_invoice: bool = True,
) -> Reservation:
    """Create one reservation and its order line inside the caller transaction.

    The inventory row must already be locked. ``stock_already_reserved`` is
    used only when transferring an existing reserved unit to a new reservation.
    """
    if Reservation.objects.filter(
        item=item,
        customer=customer,
        status__in=[Reservation.Status.ACTIVE, Reservation.Status.CONFIRMED],
    ).exists():
        raise ReservationConflict(
            "The customer already has an active or confirmed reservation for this item."
        )

    deadline = payment_deadline or timezone.now() + timedelta(
        hours=settings.PAYMENT_WINDOW_HOURS
    )
    if not stock_already_reserved and item.stock_qty - item.reserved_qty < qty:
        raise ValueError("The item has insufficient available stock.")

    reservation = Reservation.objects.create(
        item=item,
        customer=customer,
        source_comment=comment,
        qty=qty,
        status=Reservation.Status.ACTIVE,
        payment_deadline=deadline,
    )
    log_activity(
        user=actor,
        action=ActivityAction.RESERVATION_CREATED,
        target_type="Reservation",
        target_id=reservation.pk,
        new_value={
            "item_id": item.pk,
            "customer_id": customer.pk,
            "source_comment_id": comment.pk,
            "qty": reservation.qty,
            "status": reservation.status,
            "payment_deadline": reservation.payment_deadline,
        },
    )
    if not stock_already_reserved:
        reserve(item, qty, actor=actor)
    order = _unpaid_order(customer, actor=actor)
    _add_order_item(order, item, qty=qty, unit_price=unit_price, actor=actor)
    _recompute_order_total(order, actor=actor)

    if preserve_comment_outcome:
        comment.reservation = reservation
        comment.save(update_fields=["reservation"])
    else:
        _set_comment_outcome(
            comment,
            LiveComment.Outcome.RESERVED,
            reservation=reservation,
        )
    _log_comment_outcome(
        comment,
        action=ActivityAction.MINE_RESERVED,
        new_value={
            "outcome": comment.outcome,
            "reservation_id": reservation.pk,
            "order_id": order.pk,
        },
    )
    if enqueue_invoice:
        transaction.on_commit(
            partial(
                enqueue_invoice_after_commit,
                order.pk,
                reason="reservation created",
                caller="create_reservation_for_comment",
            )
        )
    return reservation


def _process_new_comment(
    comment: LiveComment, code: str | None
) -> Reservation | BackupPosition | InvalidMine:
    """Apply the detector and reservation rules for a newly persisted comment."""
    if code is None:
        return _set_invalid(
            comment,
            outcome=LiveComment.Outcome.IGNORED,
            action=ActivityAction.MINE_IGNORED,
            reason="Message did not match the mining pattern.",
        )

    comment.parsed_code = code
    comment.save(update_fields=["parsed_code"])
    item = validate_item(code)
    if item is None:
        return _set_invalid(
            comment,
            outcome=LiveComment.Outcome.INVALID,
            action=ActivityAction.MINE_INVALID,
            reason="Unknown, archived, or sold mining code.",
        )

    item = lock_item(item)
    if (
        item.is_archived
        or item.availability_status == InventoryItem.AvailabilityStatus.SOLD
    ):
        return _set_invalid(
            comment,
            outcome=LiveComment.Outcome.INVALID,
            action=ActivityAction.MINE_INVALID,
            reason="Unknown, archived, or sold mining code.",
        )
    customer = get_or_create_customer(comment)
    return attempt_reservation(item, customer, comment)


def process_comment(
    comment_data: CommentData,
) -> Reservation | BackupPosition | InvalidMine:
    """Parse a comment, reserve or queue its item, and enqueue invoicing on commit."""
    existing = LiveComment.objects.filter(
        fb_comment_id=comment_data["fb_comment_id"]
    ).first()
    if existing is not None:
        return _record_result(existing)

    normalized_message = normalize_message(comment_data["message"])
    code = parse_code(normalized_message)
    values = {
        **comment_data,
        "raw_message": comment_data["message"],
        "message": normalized_message,
        "parsed_code": code,
    }

    try:
        with transaction.atomic():
            try:
                with transaction.atomic():
                    comment = LiveComment.objects.create(**values)
            except IntegrityError:
                existing = LiveComment.objects.filter(
                    fb_comment_id=comment_data["fb_comment_id"]
                ).first()
                if existing is not None:
                    return _record_result(existing)
                raise
            return _process_new_comment(comment, code)
    except IntegrityError:
        existing = LiveComment.objects.filter(
            fb_comment_id=comment_data["fb_comment_id"]
        ).first()
        if existing is not None:
            return _record_result(existing)
        raise


def confirm_comment(
    comment_id: int,
    *,
    mining_code: str | None,
    actor: User,
) -> Reservation | BackupPosition:
    """Manually confirm an ignored/invalid comment through the shared claim path."""
    try:
        comment = LiveComment.objects.get(pk=comment_id)
    except LiveComment.DoesNotExist as exc:
        raise ReservationNotFound("Comment not found.") from exc

    if comment.outcome in {
        LiveComment.Outcome.FLAGGED_LATE,
        LiveComment.Outcome.RESERVED,
        LiveComment.Outcome.BACKUP,
    }:
        raise ReservationConflict(
            "This comment already has a reservation/backup or is flagged late."
        )
    if comment.outcome not in {
        LiveComment.Outcome.IGNORED,
        LiveComment.Outcome.INVALID,
    }:
        raise ReservationConflict("This comment cannot be manually confirmed.")

    code = (mining_code or comment.parsed_code or "").strip()
    if not code:
        raise ReservationValidationError("mining_code is required.")
    item = validate_item(code)
    if item is None:
        raise ReservationValidationError("Unknown or unavailable mining code.")

    with transaction.atomic():
        item = lock_item(item)
        list(
            Reservation.objects.select_for_update()
            .filter(item_id=item.pk)
            .order_by("pk")
        )
        comment = LiveComment.objects.select_for_update().get(pk=comment.pk)
        if comment.outcome not in {
            LiveComment.Outcome.IGNORED,
            LiveComment.Outcome.INVALID,
        }:
            raise ReservationConflict("The comment has already been processed.")
        if (
            item.is_archived
            or item.availability_status == InventoryItem.AvailabilityStatus.SOLD
        ):
            raise ReservationValidationError("Unknown or unavailable mining code.")
        customer = get_or_create_customer(comment, actor=actor)
        if _is_duplicate_mine(item, customer):
            raise ReservationConflict(
                "The commenter already holds or waits for this item."
            )
        previous_value = {
            "outcome": comment.outcome,
            "parsed_code": comment.parsed_code,
        }
        comment.parsed_code = code
        comment.save(update_fields=["parsed_code"])
        if item.stock_qty - item.reserved_qty > 0:
            result = create_reservation_for_comment(
                item,
                customer,
                comment,
                actor=actor,
            )
        else:
            result = add_backup_miner(item, customer, comment, actor=actor)
        log_activity(
            user=actor,
            action=ActivityAction.MINE_RESERVED
            if isinstance(result, Reservation)
            else ActivityAction.MINE_BACKUP,
            target_type="LiveComment",
            target_id=comment.pk,
            previous_value=previous_value,
            new_value={
                "outcome": comment.outcome,
                "parsed_code": comment.parsed_code,
                "reservation_id": comment.reservation_id,
                "priority": comment.priority,
            },
            reason="Manual mine confirmation.",
        )
        return result


def _lock_order_ids(order_ids: set[int]) -> dict[int, Order]:
    """Lock order rows in ascending id order after inventory/reservation locks."""
    return {
        order.pk: order
        for order in Order.objects.select_for_update()
        .filter(pk__in=order_ids)
        .order_by("pk")
    }


def _order_ids_for_customer_item(customer_id: int, item_id: int) -> set[int]:
    """Find existing orders containing an item without taking row locks."""
    return set(
        OrderItem.objects.filter(order__customer_id=customer_id, item_id=item_id)
        .order_by("order_id")
        .values_list("order_id", flat=True)
    )


def _expire_empty_order_invoice(order: Order, *, actor: User | None) -> None:
    """Mark a sent invoice expired when its last line is removed."""
    try:
        invoice = order.invoice
    except Invoice.DoesNotExist:
        return
    if invoice.status != Invoice.Status.SENT:
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


def override_reservation(
    reservation_id: int,
    *,
    new_customer_id: int,
    reason: str,
    actor: User,
) -> tuple[Reservation, Order, Order]:
    """Transfer an active reservation and its agreed order price atomically."""
    reason = reason.strip()
    if not reason:
        raise ReservationValidationError("reason is required.")
    try:
        initial = Reservation.objects.only("item_id").get(pk=reservation_id)
    except Reservation.DoesNotExist as exc:
        raise ReservationNotFound("Reservation not found.") from exc

    item = InventoryItem.objects.get(pk=initial.item_id)
    with transaction.atomic():
        item = lock_item(item)
        locked_reservations = list(
            Reservation.objects.select_for_update()
            .filter(item_id=item.pk)
            .order_by("pk")
        )
        reservation = next(
            (row for row in locked_reservations if row.pk == reservation_id), None
        )
        if reservation is None:
            raise ReservationNotFound("Reservation not found.")
        if reservation.status not in {
            Reservation.Status.ACTIVE,
            Reservation.Status.CONFIRMED,
        }:
            raise ReservationConflict(
                "Only active or confirmed reservations can be overridden."
            )
        try:
            new_customer = Customer.objects.get(pk=new_customer_id)
        except Customer.DoesNotExist as exc:
            raise ReservationNotFound("Customer not found.") from exc
        if new_customer.pk == reservation.customer_id:
            raise ReservationValidationError(
                "The new customer must differ from the current customer."
            )

        customer_ids = sorted({reservation.customer_id, new_customer.pk})
        locked_customers = {
            customer.pk: customer
            for customer in Customer.objects.select_for_update()
            .filter(pk__in=customer_ids)
            .order_by("pk")
        }
        old_customer = locked_customers[reservation.customer_id]
        new_customer = locked_customers[new_customer.pk]
        if any(
            row.customer_id == new_customer.pk
            and row.status in {Reservation.Status.ACTIVE, Reservation.Status.CONFIRMED}
            for row in locked_reservations
        ):
            raise ReservationConflict(
                "The new customer already has an active or confirmed reservation "
                "for this item."
            )

        old_order_ids = _order_ids_for_customer_item(old_customer.pk, item.pk)
        new_order_ids = set(
            Order.objects.filter(
                customer=new_customer,
                status=Order.Status.UNPAID,
            ).values_list("pk", flat=True)
        )
        locked_orders = _lock_order_ids(old_order_ids | new_order_ids)
        old_orders = [
            locked_orders[order_id]
            for order_id in sorted(old_order_ids)
            if order_id in locked_orders
        ]
        old_order = next(
            (order for order in old_orders if order.status == Order.Status.UNPAID),
            old_orders[-1] if old_orders else None,
        )
        if old_order is None or old_order.status != Order.Status.UNPAID:
            raise ReservationConflict("The current customer has no unpaid item order.")
        target_order_before = next(
            (
                locked_orders[order_id]
                for order_id in sorted(new_order_ids)
                if order_id in locked_orders
            ),
            None,
        )
        previous_new_total = (
            target_order_before.total_amount
            if target_order_before is not None
            else Decimal("0.00")
        )

        old_line = (
            OrderItem.objects.select_for_update()
            .filter(order=old_order, item=item)
            .first()
        )
        if old_line is None or old_line.quantity < reservation.qty:
            raise ReservationConflict(
                "The source order line does not match the reservation."
            )
        agreed_price = old_line.unit_price
        if target_order_before is not None:
            target_line = (
                OrderItem.objects.select_for_update()
                .filter(order=target_order_before, item=item)
                .first()
            )
            if target_line is not None and target_line.unit_price != agreed_price:
                raise ReservationConflict(
                    "The recipient's existing item has a different agreed price."
                )
        old_line_previous = {
            "quantity": old_line.quantity,
            "unit_price": old_line.unit_price,
        }
        old_line_id = old_line.pk
        remaining_quantity = old_line.quantity - reservation.qty
        if old_line.quantity == reservation.qty:
            old_line.delete()
        else:
            old_line.quantity = remaining_quantity
            old_line.save(update_fields=["quantity"])
        log_activity(
            user=actor,
            action=ActivityAction.ORDER_ITEM_UPDATED,
            target_type="OrderItem",
            target_id=old_line_id,
            previous_value=old_line_previous,
            new_value={
                "order_id": old_order.pk,
                "item_id": item.pk,
                "quantity": remaining_quantity,
                "unit_price": agreed_price,
            },
            reason="Item moved by reservation override.",
        )

        previous_reservation = {
            "customer_id": reservation.customer_id,
            "status": reservation.status,
            "payment_deadline": reservation.payment_deadline,
        }
        reservation.status = Reservation.Status.OVERRIDDEN
        reservation.save(update_fields=["status"])
        stock_reserved_before = item.reserved_qty
        new_reservation = create_reservation_for_comment(
            item,
            new_customer,
            reservation.source_comment,
            actor=actor,
            qty=reservation.qty,
            unit_price=agreed_price,
            payment_deadline=reservation.payment_deadline,
            stock_already_reserved=True,
            preserve_comment_outcome=True,
            enqueue_invoice=False,
        )
        if item.reserved_qty != stock_reserved_before:
            raise RuntimeError("Reservation override changed reserved inventory.")

        previous_old_total = old_order.total_amount
        old_total = _recompute_order_total(old_order, actor=actor)
        new_order = Order.objects.get(
            customer=new_customer,
            status=Order.Status.UNPAID,
        )
        new_total = _recompute_order_total(new_order, actor=actor)
        log_activity(
            user=actor,
            action=ActivityAction.RESERVATION_OVERRIDDEN,
            target_type="Reservation",
            target_id=reservation.pk,
            previous_value={
                **previous_reservation,
                "old_reservation_id": reservation.pk,
                "old_customer_id": old_customer.pk,
                "old_order_total": previous_old_total,
                "new_customer_id": new_customer.pk,
                "new_reservation_id": new_reservation.pk,
                "new_order_total": previous_new_total,
            },
            new_value={
                "status": reservation.status,
                "old_customer_id": old_customer.pk,
                "new_customer_id": new_customer.pk,
                "old_reservation_id": reservation.pk,
                "new_reservation_id": new_reservation.pk,
                "old_order_total": old_total,
                "new_order_total": new_total,
            },
            reason=reason,
        )
        if old_order.items.exists():
            transaction.on_commit(
                partial(
                    enqueue_invoice_after_commit,
                    old_order.pk,
                    reason="reservation overridden",
                    caller="override_reservation",
                )
            )
        else:
            _expire_empty_order_invoice(old_order, actor=actor)
        transaction.on_commit(
            partial(
                enqueue_invoice_after_commit,
                new_order.pk,
                reason="reservation overridden",
                caller="override_reservation",
            )
        )
        return new_reservation, old_order, new_order
