"""Reconcile invoice gaps for unpaid orders with live reservations."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from functools import partial
from typing import TypedDict

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from activitylog.models import ActivityLog
from activitylog.services import ActivityAction, log_activity
from invoicing.models import Invoice
from mining.models import Reservation
from orders.models import Order, OrderItem

logger = logging.getLogger("invoicing")

_LIVE_RESERVATION_STATUSES = (
    Reservation.Status.ACTIVE,
    Reservation.Status.CONFIRMED,
)
_RECENT_INVOICE_ACTIONS = (
    ActivityAction.INVOICE_CREATED,
    ActivityAction.INVOICE_UPDATED,
    ActivityAction.INVOICE_LINK_CREATED,
)


class _InvoiceGapSignature(TypedDict):
    order_total: str
    invoice_id: int | None
    invoice_status: str | None
    invoice_total: str | None
    sent_at: str | None
    has_payment_link: bool
    gaps: list[str]


def _gaps(order: Order, invoice: Invoice | None) -> tuple[str, ...]:
    if invoice is None:
        return ("no invoice",)
    gaps = []
    if invoice.status != Invoice.Status.SENT:
        gaps.append("status not SENT")
    if invoice.sent_at is None:
        gaps.append("sent_at null")
    if invoice.total != order.total_amount:
        gaps.append("total mismatch")
    if not invoice.payment_link_url:
        gaps.append("empty payment link")
    return tuple(gaps)


def _signature(
    order: Order,
    invoice: Invoice | None,
    gaps: tuple[str, ...],
) -> _InvoiceGapSignature:
    return {
        "order_total": str(order.total_amount),
        "invoice_id": invoice.pk if invoice else None,
        "invoice_status": invoice.status if invoice else None,
        "invoice_total": str(invoice.total) if invoice else None,
        "sent_at": invoice.sent_at.isoformat() if invoice and invoice.sent_at else None,
        "has_payment_link": bool(invoice and invoice.payment_link_url),
        "gaps": list(gaps),
    }


def _has_live_reservation(order: Order) -> bool:
    return Reservation.objects.filter(
        customer_id=order.customer_id,
        item_id__in=OrderItem.objects.filter(order_id=order.pk).values("item_id"),
        status__in=_LIVE_RESERVATION_STATUSES,
    ).exists()


def _has_recent_activity(
    order: Order,
    invoice: Invoice | None,
    *,
    grace_cutoff: datetime,
) -> bool:
    if Reservation.objects.filter(
        customer_id=order.customer_id,
        item_id__in=OrderItem.objects.filter(order_id=order.pk).values("item_id"),
        status__in=_LIVE_RESERVATION_STATUSES,
        created_at__gte=grace_cutoff,
    ).exists():
        return True
    if ActivityLog.objects.filter(
        target_type="Order",
        target_id=str(order.pk),
        action=ActivityAction.ORDER_TOTAL_UPDATED,
        created_at__gte=grace_cutoff,
    ).exists():
        return True
    return bool(
        invoice
        and ActivityLog.objects.filter(
            target_type="Invoice",
            target_id=str(invoice.pk),
            action__in=_RECENT_INVOICE_ACTIONS,
            created_at__gte=grace_cutoff,
        ).exists()
    )


def reconcile_unpaid_invoice_gaps() -> dict[str, int]:
    """Enqueue one idempotent invoice task for each mature invoice gap."""
    now = timezone.now()
    grace_cutoff = now - timedelta(
        seconds=settings.INVOICE_RECONCILIATION_GRACE_SECONDS
    )
    orders = list(
        Order.objects.filter(
            status=Order.Status.UNPAID,
            items__item__reservations__customer_id=F("customer_id"),
            items__item__reservations__status__in=_LIVE_RESERVATION_STATUSES,
        )
        .select_related("customer")
        .distinct()
        .order_by("pk")
    )
    order_ids = [order.pk for order in orders]
    invoices = {
        invoice.order_id: invoice
        for invoice in Invoice.objects.filter(order_id__in=order_ids)
    }
    candidate_ids = [
        order.pk for order in orders if _gaps(order, invoices.get(order.pk))
    ]
    if not candidate_ids:
        logger.info("reconciliation repaired=0 checked=%s grace_skipped=0", len(orders))
        return {"checked": len(orders), "repaired": 0, "grace_skipped": 0}

    from mining.services import enqueue_invoice_after_commit

    repaired = 0
    grace_skipped = 0
    for order_id in candidate_ids:
        with transaction.atomic():
            order = (
                Order.objects.select_for_update()
                .filter(pk=order_id, status=Order.Status.UNPAID)
                .first()
            )
            if order is None or not _has_live_reservation(order):
                continue
            invoice = Invoice.objects.filter(order_id=order.pk).first()
            gaps = _gaps(order, invoice)
            if not gaps:
                continue
            signature = _signature(order, invoice, gaps)
            recent_reconciliation = (
                ActivityLog.objects.filter(
                    target_type="Order",
                    target_id=str(order.pk),
                    action=ActivityAction.INVOICE_RECONCILIATION_ENQUEUED,
                    created_at__gte=grace_cutoff,
                )
                .order_by("-created_at", "-pk")
                .first()
            )
            if _has_recent_activity(order, invoice, grace_cutoff=grace_cutoff) or (
                recent_reconciliation and recent_reconciliation.new_value == signature
            ):
                grace_skipped += 1
                continue

            log_activity(
                user=None,
                action=ActivityAction.INVOICE_RECONCILIATION_ENQUEUED,
                target_type="Order",
                target_id=order.pk,
                new_value=signature,
                reason="Invoice gap reconciliation task enqueued.",
            )
            transaction.on_commit(
                partial(
                    enqueue_invoice_after_commit,
                    order.pk,
                    reason=", ".join(gaps),
                    caller="reconcile_unpaid_invoice_gaps",
                )
            )
            repaired += 1

    logger.info(
        "reconciliation repaired=%s checked=%s grace_skipped=%s",
        repaired,
        len(orders),
        grace_skipped,
    )
    return {
        "checked": len(orders),
        "repaired": repaired,
        "grace_skipped": grace_skipped,
    }
