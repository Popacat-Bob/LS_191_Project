from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock, patch

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from activitylog.models import ActivityLog
from invoicing.models import Invoice
from invoicing.providers import FakePaymentProvider
from invoicing.services import send_invoice
from invoicing.tasks import reconcile_unpaid_invoices
from mining.models import Reservation
from mining.services import process_comment
from orders.models import Order
from tests.factories import (
    CustomerFactory,
    InventoryItemFactory,
    InvoiceFactory,
    LiveCommentFactory,
    OrderFactory,
    OrderItemFactory,
    ReservationFactory,
)

pytestmark = pytest.mark.django_db


def _order_with_live_reservation(
    *,
    total: Decimal = Decimal("50.00"),
    order_status: str = Order.Status.UNPAID,
    reservation_status: str = Reservation.Status.ACTIVE,
):
    customer = CustomerFactory()
    item = InventoryItemFactory()
    order = OrderFactory(customer=customer, status=order_status, total_amount=total)
    OrderItemFactory(order=order, item=item, unit_price=total)
    comment = LiveCommentFactory(fb_commenter_id=customer.facebook_id)
    reservation = ReservationFactory(
        item=item,
        customer=customer,
        source_comment=comment,
        status=reservation_status,
    )
    Reservation.objects.filter(pk=reservation.pk).update(
        created_at=timezone.now() - timedelta(minutes=2)
    )
    return order


@pytest.mark.django_db(transaction=True)
def test_reconciliation_repairs_gaps_and_is_idempotent(caplog):
    missing_invoice = _order_with_live_reservation()
    unsent_invoice_order = _order_with_live_reservation()
    stale_total_order = _order_with_live_reservation()
    paid_order = _order_with_live_reservation(order_status=Order.Status.PAID)
    no_live_reservation_order = _order_with_live_reservation(
        reservation_status=Reservation.Status.EXPIRED
    )
    empty_order = OrderFactory()

    InvoiceFactory(order=unsent_invoice_order, total=Decimal("50.00"))
    InvoiceFactory(
        order=stale_total_order,
        total=Decimal("40.00"),
        status=Invoice.Status.SENT,
        sent_at=timezone.now(),
        payment_link_url="https://pay.local/stale",
    )
    InvoiceFactory(order=paid_order, total=Decimal("50.00"))
    InvoiceFactory(order=no_live_reservation_order, total=Decimal("50.00"))
    InvoiceFactory(order=empty_order, total=Decimal("0.00"))

    def enqueue_without_locks(order_id):
        assert connection.in_atomic_block is False

    with (
        override_settings(INVOICE_DELAY_SECONDS=0),
        caplog.at_level("INFO", logger="invoicing"),
        patch("mining.services.send_invoice.delay") as enqueue,
    ):
        enqueue.side_effect = enqueue_without_locks
        first = reconcile_unpaid_invoices.run()
        second = reconcile_unpaid_invoices.run()

    assert first == {"checked": 3, "repaired": 3, "grace_skipped": 0}
    assert second == {"checked": 3, "repaired": 0, "grace_skipped": 3}
    assert {call.args[0] for call in enqueue.call_args_list} == {
        missing_invoice.pk,
        unsent_invoice_order.pk,
        stale_total_order.pk,
    }
    assert (
        f"enqueue order_id={missing_invoice.pk} reason=no invoice countdown=0 "
        "caller=reconcile_unpaid_invoice_gaps"
    ) in caplog.text
    assert "reconciliation repaired=3 checked=3 grace_skipped=0" in caplog.text


@pytest.mark.django_db(transaction=True)
def test_reconciliation_retries_a_gap_after_the_grace_period():
    order = _order_with_live_reservation()
    with patch("mining.services.enqueue_invoice_after_commit") as enqueue:
        assert reconcile_unpaid_invoices.run()["repaired"] == 1
        ActivityLog.objects.filter(
            target_type="Order",
            target_id=str(order.pk),
            action="INVOICE_RECONCILIATION_ENQUEUED",
        ).update(created_at=timezone.now() - timedelta(minutes=2))
        assert reconcile_unpaid_invoices.run()["repaired"] == 1

    assert enqueue.call_count == 2


def test_invoice_audit_reports_end_to_end_gaps():
    item_codes = ("801", "802")
    for code in item_codes:
        InventoryItemFactory(mining_code=code, stock_qty=3)

    comment_index = 0
    with patch("mining.services.enqueue_invoice_after_commit"):
        for customer_index in range(3):
            for code in item_codes:
                comment_index += 1
                process_comment(
                    {
                        "fb_comment_id": f"audit-comment-{comment_index}",
                        "live_video_id": "audit-run",
                        "message": f"mine {code}",
                        "fb_commenter_id": f"audit-customer-{customer_index}",
                        "fb_commenter_name": f"Audit Customer {customer_index}",
                        "created_time": timezone.now(),
                    }
                )

    sender = Mock()
    with (
        patch(
            "invoicing.services.get_payment_provider",
            return_value=FakePaymentProvider(),
        ),
        patch("invoicing.services.get_message_sender", return_value=sender),
    ):
        for order in Order.objects.filter(status=Order.Status.UNPAID):
            send_invoice(order.pk)

    output = StringIO()
    call_command("invoice_audit", stdout=output)
    assert "Eligible orders: 3; gap orders: 0; gap conditions: 0" in output.getvalue()

    invoice = Invoice.objects.first()
    Invoice.objects.filter(pk=invoice.pk).update(sent_at=None)
    output = StringIO()
    call_command("invoice_audit", stdout=output)
    assert "sent_at null" in output.getvalue()
    assert "gap orders: 1" in output.getvalue()


def test_second_mine_after_sent_invoice_reissues_with_new_total():
    first_item = InventoryItemFactory(mining_code="811")
    second_item = InventoryItemFactory(mining_code="812")
    customer_id = "reissue-customer"

    def mine(comment_id: str, code: str) -> None:
        process_comment(
            {
                "fb_comment_id": comment_id,
                "live_video_id": "reissue-run",
                "message": f"mine {code}",
                "fb_commenter_id": customer_id,
                "fb_commenter_name": "Reissue Customer",
                "created_time": timezone.now(),
            }
        )

    sender = Mock()
    with patch("mining.services.enqueue_invoice_after_commit"):
        mine("reissue-first", "811")
        order = Order.objects.get(customer__facebook_id=customer_id)
        with (
            patch(
                "invoicing.services.get_payment_provider",
                return_value=FakePaymentProvider(),
            ),
            patch("invoicing.services.get_message_sender", return_value=sender),
        ):
            send_invoice(order.pk)
            mine("reissue-second", "812")
            order.refresh_from_db()
            invoice = send_invoice(order.pk)

    assert invoice is not None
    assert invoice.status == Invoice.Status.SENT
    assert invoice.total == order.total_amount == Decimal("200.00")
    assert len(sender.send.call_args_list) == 2
    assert "Total: PHP 200.00" in sender.send.call_args.kwargs["text"]
    assert first_item.name in sender.send.call_args.kwargs["text"]
    assert second_item.name in sender.send.call_args.kwargs["text"]


def test_invoice_task_uses_late_acknowledgement_and_worker_loss_rejection():
    from django.conf import settings

    from invoicing.tasks import send_invoice as send_invoice_task

    assert send_invoice_task.acks_late is True
    assert send_invoice_task.reject_on_worker_lost is True
    assert (
        settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"]
        > settings.INVOICE_DELAY_SECONDS
    )
    assert (
        settings.CELERY_BEAT_SCHEDULE["reconcile-unpaid-invoices"]["schedule"]
        == settings.INVOICE_RECONCILIATION_INTERVAL_SECONDS
    )


def test_invoice_audit_reports_missing_invoice_and_live_reservation():
    order = _order_with_live_reservation()
    output = StringIO()

    call_command("invoice_audit", stdout=output)

    assert f"{order.pk}" in output.getvalue()
    assert "no invoice" in output.getvalue()
    assert "gap orders: 1" in output.getvalue()
