from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Event, Lock
from unittest.mock import Mock, patch

import pytest
import requests
from celery.exceptions import Retry
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, close_old_connections, connection, connections
from django.test import override_settings
from django.utils import timezone

from activitylog.models import ActivityLog
from activitylog.services import ActivityAction
from inventory.models import InventoryItem
from invoicing.models import Invoice
from invoicing.providers import (
    FakePaymentProvider,
    PaymentLink,
    PaymentProviderError,
    PayMongoProvider,
    get_payment_provider,
)
from invoicing.senders import (
    ConsoleSender,
    GraphApiSender,
    MessageSendError,
    get_message_sender,
)
from invoicing.services import send_invoice
from invoicing.tasks import send_invoice as send_invoice_task
from mining.models import LiveComment, Reservation
from mining.services import process_comment
from orders.models import Order
from tests.factories import (
    InventoryItemFactory,
    LiveCommentFactory,
    OrderFactory,
    OrderItemFactory,
    ReservationFactory,
)

pytestmark = pytest.mark.django_db


def _order_with_line(
    *,
    total_unit_price: Decimal = Decimal("25.50"),
    quantity: int = 1,
) -> tuple[Order, InventoryItem]:
    order = OrderFactory()
    item = InventoryItemFactory(name="Vintage jacket")
    OrderItemFactory(
        order=order,
        item=item,
        quantity=quantity,
        unit_price=total_unit_price,
    )
    comment = LiveCommentFactory(
        fb_commenter_id=order.customer.facebook_id,
        outcome=LiveComment.Outcome.RESERVED,
    )
    ReservationFactory(
        item=item,
        customer=order.customer,
        source_comment=comment,
        payment_deadline=timezone.now() + timedelta(hours=12),
    )
    return order, item


def test_fake_provider_and_console_sender_create_and_send_invoice(caplog):
    order, _ = _order_with_line()
    with (
        override_settings(PAYMENT_PROVIDER="fake", MESSAGE_SENDER="console"),
        caplog.at_level("INFO", logger="invoicing"),
    ):
        invoice = send_invoice(order.pk)

    assert invoice is not None
    assert invoice.invoice_number.startswith("INV-")
    assert invoice.total == Decimal("25.50")
    assert invoice.paymongo_link_id == f"fake-{invoice.invoice_number}"
    assert invoice.payment_link_url.endswith(invoice.invoice_number)
    assert invoice.sent_at is not None
    assert invoice.status == Invoice.Status.SENT
    assert f"-> Messenger to {order.customer.full_name}" in caplog.text
    assert "Vintage jacket" in caplog.text
    assert "PHP 25.50" in caplog.text
    assert invoice.payment_link_url in caplog.text
    assert f"link created order_id={order.pk}" in caplog.text
    assert f"message sent order_id={order.pk}" in caplog.text


def test_invoice_total_uses_agreed_decimal_prices_not_current_item_prices():
    order = OrderFactory()
    first_item = InventoryItemFactory(selling_price=Decimal("999.00"))
    second_item = InventoryItemFactory(selling_price=Decimal("999.00"))
    OrderItemFactory(
        order=order,
        item=first_item,
        quantity=2,
        unit_price=Decimal("15.25"),
    )
    OrderItemFactory(
        order=order,
        item=second_item,
        quantity=3,
        unit_price=Decimal("5.10"),
    )
    first_item.selling_price = Decimal("1.00")
    first_item.save(update_fields=["selling_price"])
    second_item.selling_price = Decimal("2.00")
    second_item.save(update_fields=["selling_price"])

    invoice = send_invoice(order.pk)

    assert invoice is not None
    assert invoice.total == Decimal("45.80")


def test_invoice_uses_earliest_active_reservation_deadline_and_comment_context():
    order, first_item = _order_with_line()
    earliest = timezone.now() + timedelta(hours=2)
    latest = timezone.now() + timedelta(hours=10)
    first_reservation = Reservation.objects.get(
        item=first_item,
        customer=order.customer,
    )
    first_reservation.payment_deadline = earliest
    first_reservation.save(update_fields=["payment_deadline"])

    second_item = InventoryItemFactory(name="Second piece")
    OrderItemFactory(
        order=order,
        item=second_item,
        quantity=1,
        unit_price=Decimal("10.00"),
    )
    second_comment = LiveCommentFactory(
        fb_commenter_id=order.customer.facebook_id,
        fb_comment_id="later-source-comment",
        outcome=LiveComment.Outcome.RESERVED,
    )
    ReservationFactory(
        item=second_item,
        customer=order.customer,
        source_comment=second_comment,
        payment_deadline=latest,
    )
    sender = Mock()
    with patch("invoicing.services.get_message_sender", return_value=sender):
        invoice = send_invoice(order.pk)

    assert invoice is not None
    assert invoice.payment_deadline == earliest
    assert sender.send.call_args.kwargs["context"] == {
        "order_id": order.pk,
        "source_comment_id": first_reservation.source_comment.fb_comment_id,
        "recipient_name": order.customer.full_name,
    }


@pytest.mark.django_db(transaction=True)
def test_invoice_is_stored_before_provider_failure():
    order, _ = _order_with_line()

    class FailingProvider:
        def create_payment_link(self, **kwargs):
            invoice = Invoice.objects.get(order=order)
            assert invoice.status == Invoice.Status.DRAFT
            assert invoice.total == Decimal("25.50")
            assert not invoice.paymongo_link_id
            assert connection.in_atomic_block is False
            raise PaymentProviderError("temporary failure")

    with patch(
        "invoicing.services.get_payment_provider",
        return_value=FailingProvider(),
    ):
        with pytest.raises(PaymentProviderError):
            send_invoice(order.pk)

    invoice = Invoice.objects.get(order=order)
    assert invoice.status == Invoice.Status.DRAFT
    assert not invoice.paymongo_link_id
    assert invoice.sent_at is None


def test_provider_failure_then_retry_succeeds():
    order, _ = _order_with_line()
    provider = Mock()
    provider.create_payment_link.side_effect = [
        PaymentProviderError("temporary"),
        PaymentLink("link-1", "https://fake-pay.local/links/1"),
    ]
    with (
        patch("invoicing.services.get_payment_provider", return_value=provider),
        patch("invoicing.services.get_message_sender", return_value=ConsoleSender()),
    ):
        with pytest.raises(PaymentProviderError):
            send_invoice(order.pk)
        invoice = send_invoice(order.pk)

    assert invoice is not None
    assert invoice.status == Invoice.Status.SENT
    assert provider.create_payment_link.call_count == 2


def test_sender_failure_retry_reuses_stored_payment_link():
    order, _ = _order_with_line()
    provider = Mock()
    provider.create_payment_link.return_value = PaymentLink(
        "link-once", "https://fake-pay.local/links/once"
    )
    sender = Mock()
    sender.send.side_effect = [MessageSendError("temporary"), None]
    with (
        patch("invoicing.services.get_payment_provider", return_value=provider),
        patch("invoicing.services.get_message_sender", return_value=sender),
    ):
        with pytest.raises(MessageSendError):
            send_invoice(order.pk)
        invoice_after_failure = Invoice.objects.get(order=order)
        assert invoice_after_failure.paymongo_link_id == "link-once"
        assert invoice_after_failure.status == Invoice.Status.DRAFT
        invoice = send_invoice(order.pk)

    assert invoice is not None
    assert invoice.status == Invoice.Status.SENT
    provider.create_payment_link.assert_called_once()
    assert sender.send.call_count == 2


def test_send_invoice_is_idempotent_for_already_sent_total():
    order, _ = _order_with_line()
    sender = Mock()
    with (
        patch("invoicing.services.get_message_sender", return_value=sender),
        patch(
            "invoicing.services.get_payment_provider",
            return_value=FakePaymentProvider(),
        ) as provider_factory,
    ):
        first = send_invoice(order.pk)
        sent_at = first.sent_at
        second = send_invoice(order.pk)

    assert first.pk == second.pk
    assert second.sent_at == sent_at
    sender.send.assert_called_once()
    provider_factory.assert_called_once()


def test_invoice_skip_reasons_are_logged_at_warning(caplog):
    empty_order = OrderFactory()
    paid_order = OrderFactory(status=Order.Status.PAID)

    with caplog.at_level("WARNING", logger="invoicing"):
        assert send_invoice(empty_order.pk) is None
        assert send_invoice(paid_order.pk) is None
        assert send_invoice(987654321) is None

    assert f"skip order_id={empty_order.pk} reason=no items" in caplog.text
    assert f"skip order_id={paid_order.pk} reason=not UNPAID" in caplog.text
    assert "skip order_id=987654321 reason=order missing" in caplog.text


@pytest.mark.django_db(transaction=True)
def test_concurrent_invoice_sends_reissue_when_order_total_changes():
    order, _ = _order_with_line()
    send_lock = Lock()
    first_sending = Event()
    second_sending = Event()
    release_first_send = Event()
    release_second_send = Event()
    messages = []
    sender = Mock()
    send_count = 0

    def send_message(**kwargs):
        nonlocal send_count
        messages.append(kwargs["text"])
        with send_lock:
            send_count += 1
            current_send = send_count
        entered = first_sending if current_send == 1 else second_sending
        release = release_first_send if current_send == 1 else release_second_send
        entered.set()
        assert release.wait(timeout=10)

    sender.send.side_effect = send_message

    def send_in_thread():
        close_old_connections()
        try:
            return send_invoice(order.pk)
        finally:
            connections.close_all()

    with (
        patch("invoicing.services.get_message_sender", return_value=sender),
        patch(
            "invoicing.services.get_payment_provider",
            return_value=FakePaymentProvider(),
        ),
        patch("mining.services.enqueue_invoice_after_commit") as enqueue,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        futures = [executor.submit(send_in_thread)]
        assert first_sending.wait(timeout=10)
        futures.append(executor.submit(send_in_thread))
        assert second_sending.wait(timeout=10)
        line = order.items.get()
        order.items.filter(pk=line.pk).update(quantity=2)
        Order.objects.filter(pk=order.pk).update(total_amount=Decimal("51.00"))
        release_first_send.set()
        first_result = futures[0].result(timeout=10)
        release_second_send.set()
        second_result = futures[1].result(timeout=10)

    assert enqueue.call_count == 2
    with (
        patch("invoicing.services.get_message_sender", return_value=sender),
        patch(
            "invoicing.services.get_payment_provider",
            return_value=FakePaymentProvider(),
        ),
    ):
        send_invoice(order.pk)

    order.refresh_from_db()
    invoice = Invoice.objects.get(order=order)
    assert invoice.total == order.total_amount
    assert invoice.sent_at is not None
    assert first_result is not None
    assert second_result is not None
    assert "Total: PHP 51.00" in messages[-1]


def test_changed_total_reissues_link_and_logs_previous_and_new_totals():
    order, first_item = _order_with_line()
    second_item = InventoryItemFactory(name="Woven bag")
    provider = Mock()
    provider.create_payment_link.side_effect = [
        PaymentLink("link-1", "https://fake-pay.local/links/1"),
        PaymentLink("link-2", "https://fake-pay.local/links/2"),
    ]
    sender = Mock()
    with (
        patch("invoicing.services.get_payment_provider", return_value=provider),
        patch("invoicing.services.get_message_sender", return_value=sender),
    ):
        first_invoice = send_invoice(order.pk)
        OrderItemFactory(
            order=order,
            item=second_item,
            quantity=2,
            unit_price=Decimal("7.25"),
        )
        second_invoice = send_invoice(order.pk)

    assert first_invoice is not None and second_invoice is not None
    assert second_invoice.total == Decimal("40.00")
    assert second_invoice.paymongo_link_id == "link-2"
    assert second_invoice.sent_at > first_invoice.sent_at
    assert provider.create_payment_link.call_count == 2
    assert sender.send.call_count == 2
    change_log = ActivityLog.objects.get(
        action=ActivityAction.INVOICE_UPDATED,
        target_type="Invoice",
        target_id=str(first_invoice.pk),
        reason="order total changed",
    )
    assert change_log.previous_value["total"] == "25.50"
    assert change_log.previous_value["paymongo_link_id"] == "link-1"
    assert change_log.new_value["total"] == "40.00"
    assert first_item.selling_price == Decimal("100.00")


@pytest.mark.parametrize(
    ("status", "with_items"),
    [(Order.Status.PAID, True), (Order.Status.UNPAID, False)],
)
def test_non_unpaid_or_empty_order_is_skipped(status, with_items):
    order = OrderFactory(status=status)
    if with_items:
        OrderItemFactory(order=order)

    assert send_invoice(order.pk) is None
    assert not Invoice.objects.filter(order=order).exists()
    assert ActivityLog.objects.filter(
        action=ActivityAction.INVOICE_SKIPPED,
        target_id=str(order.pk),
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_provider_and_sender_are_called_outside_transactions():
    order, _ = _order_with_line()
    provider = Mock()
    sender = Mock()

    def create_link(**kwargs):
        assert connection.in_atomic_block is False
        return PaymentLink("unlocked-link", "https://fake-pay.local/unlocked")

    def send_message(**kwargs):
        assert connection.in_atomic_block is False

    provider.create_payment_link.side_effect = create_link
    sender.send.side_effect = send_message
    with (
        patch("invoicing.services.get_payment_provider", return_value=provider),
        patch("invoicing.services.get_message_sender", return_value=sender),
    ):
        assert send_invoice(order.pk).status == Invoice.Status.SENT


def test_paymongo_provider_uses_basic_auth_centavos_and_timeout():
    response = Mock()
    response.json.return_value = {
        "data": {
            "id": "paymongo-id",
            "attributes": {"checkout_url": "https://pay.local"},
        }
    }
    with (
        override_settings(PAYMONGO_SECRET_KEY="test-secret"),
        patch("invoicing.providers.requests.post", return_value=response) as post,
    ):
        provider = PayMongoProvider()
        link = provider.create_payment_link(
            amount=Decimal("12.34"),
            description="Invoice",
            reference="INV-1",
        )

    assert link == PaymentLink("paymongo-id", "https://pay.local")
    response.raise_for_status.assert_called_once()
    assert post.call_args.kwargs["auth"] == ("test-secret", "")
    assert post.call_args.kwargs["timeout"] > 0
    assert post.call_args.kwargs["json"]["data"]["attributes"]["amount"] == 1234
    assert isinstance(
        post.call_args.kwargs["json"]["data"]["attributes"]["amount"], int
    )


def test_paymongo_provider_converts_http_failure_to_domain_error():
    response = Mock()
    response.raise_for_status.side_effect = requests.HTTPError("bad response")
    with (
        override_settings(PAYMONGO_SECRET_KEY="test-secret"),
        patch("invoicing.providers.requests.post", return_value=response),
    ):
        provider = PayMongoProvider()
        with pytest.raises(PaymentProviderError):
            provider.create_payment_link(
                amount=Decimal("1.00"),
                description="Invoice",
                reference="INV-1",
            )


def test_paymongo_provider_requires_secret_key():
    with override_settings(PAYMONGO_SECRET_KEY=""):
        with pytest.raises(ImproperlyConfigured):
            PayMongoProvider()


def test_provider_and_sender_factories_and_graph_sender_stub():
    with override_settings(PAYMENT_PROVIDER="fake", MESSAGE_SENDER="console"):
        assert isinstance(get_payment_provider(), FakePaymentProvider)
        assert isinstance(get_message_sender(), ConsoleSender)
    with override_settings(PAYMENT_PROVIDER="nope"):
        with pytest.raises(ImproperlyConfigured):
            get_payment_provider()
    with override_settings(MESSAGE_SENDER="nope"):
        with pytest.raises(ImproperlyConfigured):
            get_message_sender()

    with pytest.raises(NotImplementedError, match="Graph API sending is deferred"):
        GraphApiSender().send(
            recipient_facebook_id="fb-id",
            text="invoice",
            context={"order_id": 1, "source_comment_id": "comment-1"},
        )


@pytest.mark.parametrize(
    "error",
    [NotImplementedError, ImproperlyConfigured],
)
def test_task_does_not_retry_configuration_or_stub_errors(error):
    with (
        patch("invoicing.tasks.send_invoice_service", side_effect=error),
        patch.object(send_invoice_task, "retry") as retry,
    ):
        with pytest.raises(error):
            send_invoice_task.run(1)
    retry.assert_not_called()
    assert error not in send_invoice_task.autoretry_for


def test_task_retries_transient_database_errors():
    with (
        patch(
            "invoicing.tasks.send_invoice_service",
            side_effect=OperationalError("temporary"),
        ),
        patch.object(send_invoice_task, "retry", side_effect=Retry()) as retry,
    ):
        with pytest.raises(Retry):
            send_invoice_task.run(1)
    assert OperationalError in send_invoice_task.autoretry_for
    retry.assert_called_once()
    assert isinstance(retry.call_args.kwargs["exc"], OperationalError)


def test_invoice_delay_is_passed_to_celery_enqueue():
    from mining.services import enqueue_invoice_after_commit

    with (
        override_settings(INVOICE_DELAY_SECONDS=23),
        patch("mining.services.send_invoice.apply_async") as apply_async,
    ):
        enqueue_invoice_after_commit(42)

    apply_async.assert_called_once_with(args=[42], countdown=23)


def test_process_comment_on_commit_delivers_invoice_end_to_end(
    caplog, django_capture_on_commit_callbacks
):
    item = InventoryItemFactory(mining_code="101", name="Live dress", stock_qty=1)
    data = {
        "fb_comment_id": "end-to-end-comment",
        "live_video_id": "live-1",
        "message": "mine 101",
        "fb_commenter_id": "e2e-buyer",
        "fb_commenter_name": "A Buyer",
        "created_time": timezone.now(),
    }
    with (
        override_settings(
            CELERY_TASK_ALWAYS_EAGER=True,
            CELERY_TASK_EAGER_PROPAGATES=True,
            PAYMENT_PROVIDER="fake",
            MESSAGE_SENDER="console",
            INVOICE_DELAY_SECONDS=0,
        ),
        caplog.at_level("INFO", logger="invoicing.senders"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        reservation = process_comment(data)

    assert isinstance(reservation, Reservation)
    invoice = Invoice.objects.get(order__customer__facebook_id="e2e-buyer")
    assert invoice.status == Invoice.Status.SENT
    assert invoice.total == item.selling_price
    assert invoice.paymongo_link_id
    assert "Live dress" in caplog.text
