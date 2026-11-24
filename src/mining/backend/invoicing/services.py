"""Invoice preparation, payment-link creation, and customer messaging."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import partial

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.timezone import localdate

from activitylog.services import ActivityAction, log_activity
from invoicing.models import Invoice
from invoicing.providers import PaymentLink, get_payment_provider
from invoicing.senders import get_message_sender
from mining.models import Reservation
from orders.models import Order, OrderItem

logger = logging.getLogger("invoicing")


class InvoiceSkipped(Exception):
    """Raised by the API service when an order cannot receive an invoice."""


class InvoiceOrderNotFound(Exception):
    """Raised when an API invoice request references a missing order."""


@dataclass(frozen=True)
class _InvoicePlan:
    """Data captured while preparing an invoice in a short transaction."""

    invoice: Invoice
    order: Order
    lines: tuple[OrderItem, ...]
    total: Decimal
    source_comment_id: str
    already_sent: bool


def _order_total(lines: list[OrderItem]) -> Decimal:
    """Sum agreed order-line prices without converting through float."""
    return sum(
        (line.quantity * line.unit_price for line in lines),
        Decimal("0.00"),
    )


def _invoice_number(order: Order) -> str:
    """Build the stable, unique display number for an order invoice."""
    return f"INV-{localdate():%Y%m%d}-{order.pk:06d}"


def _active_reservations(order: Order, lines: list[OrderItem]) -> list[Reservation]:
    """Return active reservations for the items represented in this order."""
    item_ids = [line.item_id for line in lines]
    return list(
        Reservation.objects.filter(
            customer=order.customer,
            item_id__in=item_ids,
            status=Reservation.Status.ACTIVE,
        )
        .select_related("source_comment")
        .order_by(
            "payment_deadline",
            "source_comment__created_time",
            "source_comment__fb_comment_id",
        )
    )


def _deadline(reservations: list[Reservation]) -> datetime:
    """Choose the earliest active reservation deadline or a configured fallback."""
    if reservations:
        return min(reservation.payment_deadline for reservation in reservations)
    return timezone.now() + timedelta(hours=settings.PAYMENT_WINDOW_HOURS)


def _prepare_invoice(order_id: int) -> _InvoicePlan | None:
    """Lock briefly, snapshot the total, and persist the draft before I/O."""
    with transaction.atomic():
        order = (
            Order.objects.select_for_update(of=("self",))
            .select_related("customer")
            .filter(pk=order_id)
            .first()
        )
        if order is None:
            logger.warning("skip order_id=%s reason=order missing", order_id)
            return None
        lines = list(
            OrderItem.objects.filter(order=order).select_related("item").order_by("pk")
        )
        if order.status != Order.Status.UNPAID or not lines:
            reason = "not UNPAID" if order.status != Order.Status.UNPAID else "no items"
            logger.warning("skip order_id=%s reason=%s", order.pk, reason)
            log_activity(
                user=None,
                action=ActivityAction.INVOICE_SKIPPED,
                target_type="Order",
                target_id=order.pk,
                new_value={"status": order.status},
                reason=reason,
            )
            return None

        total = _order_total(lines)
        if order.total_amount != total:
            previous_total = order.total_amount
            order.total_amount = total
            order.save(update_fields=["total_amount"])
            log_activity(
                user=None,
                action=ActivityAction.ORDER_TOTAL_UPDATED,
                target_type="Order",
                target_id=order.pk,
                previous_value={"total_amount": previous_total},
                new_value={"total_amount": total},
                reason="Invoice preparation synchronized the order total to its lines.",
            )
        reservations = _active_reservations(order, lines)
        payment_deadline = _deadline(reservations)
        source_comment_id = (
            reservations[0].source_comment.fb_comment_id if reservations else ""
        )
        invoice, created = Invoice.objects.get_or_create(
            order=order,
            defaults={
                "invoice_number": _invoice_number(order),
                "total": total,
                "payment_deadline": payment_deadline,
            },
        )
        if created:
            log_activity(
                user=None,
                action=ActivityAction.INVOICE_CREATED,
                target_type="Invoice",
                target_id=invoice.pk,
                new_value={
                    "invoice_number": invoice.invoice_number,
                    "order_id": order.pk,
                    "total": invoice.total,
                    "payment_deadline": invoice.payment_deadline,
                    "status": invoice.status,
                },
            )

        already_sent = invoice.sent_at is not None and invoice.total == total
        if already_sent:
            logger.warning(
                "skip order_id=%s reason=already sent for same total total=%s",
                order.pk,
                total,
            )
            return _InvoicePlan(
                invoice=invoice,
                order=order,
                lines=tuple(lines),
                total=total,
                source_comment_id=source_comment_id,
                already_sent=True,
            )

        fields_to_update: list[str] = []
        if invoice.total != total:
            logger.info(
                "total-changed reissue order_id=%s previous_total=%s new_total=%s",
                order.pk,
                invoice.total,
                total,
            )
            previous_value = {
                "total": invoice.total,
                "paymongo_link_id": invoice.paymongo_link_id,
                "status": invoice.status,
                "sent_at": invoice.sent_at,
            }
            old_link_id = invoice.paymongo_link_id
            invoice.total = total
            invoice.paymongo_link_id = ""
            invoice.payment_link_url = ""
            invoice.sent_at = None
            invoice.status = Invoice.Status.DRAFT
            fields_to_update.extend(
                ["total", "paymongo_link_id", "payment_link_url", "sent_at", "status"]
            )
            log_activity(
                user=None,
                action=ActivityAction.INVOICE_UPDATED,
                target_type="Invoice",
                target_id=invoice.pk,
                previous_value=previous_value,
                new_value={
                    "total": total,
                    "old_link_id": old_link_id,
                    "status": invoice.status,
                    "sent_at": None,
                },
                reason="order total changed",
            )
        if invoice.payment_deadline != payment_deadline:
            previous_deadline = invoice.payment_deadline
            invoice.payment_deadline = payment_deadline
            fields_to_update.append("payment_deadline")
            log_activity(
                user=None,
                action=ActivityAction.INVOICE_UPDATED,
                target_type="Invoice",
                target_id=invoice.pk,
                previous_value={"payment_deadline": previous_deadline},
                new_value={"payment_deadline": payment_deadline},
                reason="Active reservation deadline changed.",
            )
        if fields_to_update:
            invoice.save(update_fields=fields_to_update)

        return _InvoicePlan(
            invoice=invoice,
            order=order,
            lines=tuple(lines),
            total=total,
            source_comment_id=source_comment_id,
            already_sent=False,
        )


def _store_payment_link(
    invoice_id: int, total: Decimal, link: PaymentLink
) -> tuple[Invoice, bool]:
    """Persist a created link in a short transaction, without holding I/O locks."""
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
        if invoice.total != total:
            logger.warning(
                "skip order_id=%s reason=invoice total changed before link stored "
                "expected_total=%s current_total=%s",
                invoice.order_id,
                total,
                invoice.total,
            )
            return invoice, False
        if invoice.sent_at is not None:
            logger.warning(
                "skip order_id=%s reason=already sent for same total total=%s",
                invoice.order_id,
                total,
            )
            return invoice, False
        if invoice.paymongo_link_id:
            return invoice, True

        invoice.paymongo_link_id = link.link_id
        invoice.payment_link_url = link.url
        invoice.save(update_fields=["paymongo_link_id", "payment_link_url"])
        log_activity(
            user=None,
            action=ActivityAction.INVOICE_LINK_CREATED,
            target_type="Invoice",
            target_id=invoice.pk,
            new_value={
                "paymongo_link_id": invoice.paymongo_link_id,
                "payment_link_url": invoice.payment_link_url,
                "total": invoice.total,
            },
        )
        logger.info(
            "link created order_id=%s invoice_id=%s total=%s",
            invoice.order_id,
            invoice.pk,
            invoice.total,
        )
        return invoice, True


def _message_text(plan: _InvoicePlan, invoice: Invoice) -> str:
    """Format an itemized invoice message using the agreed line prices."""
    item_lines = "\n".join(
        f"- {line.item.name} x {line.quantity} @ PHP {line.unit_price:,.2f}"
        for line in plan.lines
    )
    deadline = timezone.localtime(invoice.payment_deadline).strftime(
        "%b %d, %Y %I:%M %p"
    )
    return (
        f"Invoice {invoice.invoice_number}\n"
        f"{item_lines}\n"
        f"Total: PHP {plan.total:,.2f}\n"
        f"Pay here: {invoice.payment_link_url}\n"
        f"Payment deadline: {deadline}"
    )


def _mark_sent(order_id: int, invoice_id: int, total: Decimal) -> Invoice:
    """Set SENT only while both the order and invoice still have this total."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
        if order.total_amount != total:
            logger.warning(
                "skip order_id=%s reason=order total changed before mark sent "
                "sent_total=%s current_total=%s",
                order_id,
                total,
                order.total_amount,
            )
            return invoice
        if invoice.total != total:
            logger.warning(
                "skip order_id=%s reason=invoice total changed before mark sent "
                "sent_total=%s current_total=%s",
                order_id,
                total,
                invoice.total,
            )
            return invoice
        if invoice.sent_at is not None:
            logger.warning(
                "skip order_id=%s reason=already sent for same total total=%s",
                order_id,
                total,
            )
            return invoice
        sent_at = timezone.now()
        previous_value = {"status": invoice.status, "sent_at": invoice.sent_at}
        updated = Invoice.objects.filter(
            pk=invoice.pk,
            total=total,
            sent_at__isnull=True,
        ).update(status=Invoice.Status.SENT, sent_at=sent_at)
        if updated:
            invoice.status = Invoice.Status.SENT
            invoice.sent_at = sent_at
            log_activity(
                user=None,
                action=ActivityAction.INVOICE_SENT,
                target_type="Invoice",
                target_id=invoice.pk,
                previous_value=previous_value,
                new_value={
                    "status": invoice.status,
                    "sent_at": sent_at,
                    "total": total,
                },
            )
        invoice.refresh_from_db()
        return invoice


def _enqueue_reissue_if_total_changed(order_id: int, sent_total: Decimal) -> None:
    current_total = (
        Order.objects.filter(pk=order_id).values_list("total_amount", flat=True).first()
    )
    if current_total is None or current_total == sent_total:
        return

    logger.info(
        "total-changed reissue order_id=%s sent_total=%s current_total=%s",
        order_id,
        sent_total,
        current_total,
    )
    from mining.services import enqueue_invoice_after_commit

    transaction.on_commit(
        partial(
            enqueue_invoice_after_commit,
            order_id,
            reason="total changed during invoice send",
            caller="send_invoice",
        )
    )


def send_invoice(order_id: int) -> Invoice | None:
    """Create/link/send an unpaid order's invoice with no lock across external I/O."""
    plan = _prepare_invoice(order_id)
    if plan is None:
        return None
    if plan.already_sent:
        return plan.invoice

    invoice = plan.invoice
    if not invoice.paymongo_link_id:
        provider = get_payment_provider()
        link = provider.create_payment_link(
            amount=plan.total,
            description=f"EasyLV invoice {invoice.invoice_number}",
            reference=invoice.invoice_number,
        )
        invoice, should_send = _store_payment_link(invoice.pk, plan.total, link)
        if not should_send:
            _enqueue_reissue_if_total_changed(order_id, plan.total)
            return invoice

    sender = get_message_sender()
    sender.send(
        recipient_facebook_id=plan.order.customer.facebook_id,
        text=_message_text(plan, invoice),
        context={
            "order_id": plan.order.pk,
            "source_comment_id": plan.source_comment_id,
            "recipient_name": plan.order.customer.full_name,
        },
    )
    logger.info(
        "message sent order_id=%s invoice_id=%s total=%s",
        plan.order.pk,
        invoice.pk,
        plan.total,
    )
    sent_invoice = _mark_sent(order_id, invoice.pk, plan.total)
    _enqueue_reissue_if_total_changed(order_id, plan.total)
    return sent_invoice


def send_invoice_for_order(order_id: int) -> Invoice:
    """API-facing service that reports skipped orders as a domain conflict."""
    try:
        invoice = send_invoice(order_id)
    except Order.DoesNotExist as exc:
        raise InvoiceOrderNotFound(f"Order {order_id} was not found.") from exc
    if invoice is not None:
        return invoice

    order = Order.objects.filter(pk=order_id).first()
    if order is None:
        raise InvoiceOrderNotFound(f"Order {order_id} was not found.")
    reason = (
        "Order is not UNPAID."
        if order.status != Order.Status.UNPAID
        else "Order has no items."
    )
    raise InvoiceSkipped(reason)
