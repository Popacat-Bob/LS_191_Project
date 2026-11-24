from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

import pytest
from django.db import (
    DatabaseError,
    OperationalError,
    close_old_connections,
    connection,
    connections,
    transaction,
)
from django.utils import timezone

from activitylog.models import ActivityLog
from inventory.models import InventoryItem
from invoicing.models import Invoice
from mining import expiry
from mining.models import BackupMiner, LiveComment, Reservation
from mining.services import ReservationConflict, override_reservation, process_comment
from orders.models import Order, OrderItem
from tests.factories import (
    BackupMinerFactory,
    CustomerFactory,
    InventoryItemFactory,
    InvoiceFactory,
    LiveCommentFactory,
    OrderItemFactory,
    ReservationFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


def _mine(
    *,
    comment_id: str,
    customer_id: str,
    created_time: datetime | None = None,
    code: str = "101",
):
    return process_comment(
        {
            "fb_comment_id": comment_id,
            "live_video_id": "live-1",
            "message": f"mine {code}",
            "fb_commenter_id": customer_id,
            "fb_commenter_name": customer_id,
            "created_time": created_time or timezone.now(),
        }
    )


def _due(reservation: Reservation) -> Reservation:
    reservation.payment_deadline = timezone.now() - timedelta(minutes=1)
    reservation.save(update_fields=["payment_deadline"])
    return reservation


def test_expiry_releases_then_promotes_first_backup_and_recomputes_order():
    item = InventoryItemFactory(mining_code="101", stock_qty=1)
    base = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    with patch("invoicing.tasks.send_invoice.delay"):
        holder = _mine(
            comment_id="holder",
            customer_id="holder-buyer",
            created_time=base + timedelta(seconds=5),
        )
        flagged = _mine(
            comment_id="early-flagged",
            customer_id="early-buyer",
            created_time=base,
        )
        _mine(
            comment_id="later-backup",
            customer_id="later-buyer",
            created_time=base + timedelta(seconds=8),
        )

    assert isinstance(holder, Reservation)
    assert flagged.comment.outcome == LiveComment.Outcome.FLAGGED_LATE
    old_order = Order.objects.get(customer__facebook_id="holder-buyer")
    released_line_id = OrderItem.objects.get(order=old_order, item=item).pk
    OrderItemFactory(
        order=old_order,
        item=InventoryItemFactory(mining_code="other-item"),
        quantity=2,
        unit_price=Decimal("3.25"),
    )
    old_order.total_amount = Decimal("6.50") + item.selling_price
    old_order.save(update_fields=["total_amount"])
    _due(holder)

    with (
        patch("invoicing.tasks.send_invoice.delay"),
        patch("mining.services.send_invoice.delay"),
    ):
        changed = expiry.expire_reservation(holder.pk)

    assert changed is True
    holder.refresh_from_db()
    item.refresh_from_db()
    flagged.comment.refresh_from_db()
    promoted = Reservation.objects.get(customer__facebook_id="early-buyer")
    promoted_backup = BackupMiner.objects.get(comment=flagged.comment)
    assert holder.status == Reservation.Status.EXPIRED
    assert item.reserved_qty == 1
    assert item.availability_status == InventoryItem.AvailabilityStatus.RESERVED
    assert promoted.status == Reservation.Status.ACTIVE
    assert promoted.payment_deadline > timezone.now()
    assert promoted.source_comment == flagged.comment
    assert flagged.comment.outcome == LiveComment.Outcome.FLAGGED_LATE
    assert flagged.comment.reservation_id == promoted.pk
    assert promoted_backup.status == BackupMiner.Status.PROMOTED
    old_order.refresh_from_db()
    assert old_order.total_amount == Decimal("6.50")
    new_order = Order.objects.get(customer__facebook_id="early-buyer")
    assert new_order.total_amount == item.selling_price
    assert not OrderItem.objects.filter(order=old_order, item=item).exists()
    assert ActivityLog.objects.filter(
        action="ORDER_ITEM_UPDATED",
        target_type="OrderItem",
        target_id=str(released_line_id),
        reason="Reservation released.",
    ).exists()


def test_expire_twice_releases_and_promotes_only_once():
    item = InventoryItemFactory(mining_code="101", stock_qty=1)
    with patch("invoicing.tasks.send_invoice.delay"):
        holder = _mine(comment_id="holder", customer_id="holder")
        _mine(
            comment_id="backup",
            customer_id="backup",
            created_time=timezone.now() + timedelta(seconds=1),
        )
    _due(holder)

    assert expiry.expire_reservation(holder.pk) is True
    assert expiry.expire_reservation(holder.pk) is False
    item.refresh_from_db()
    assert item.reserved_qty == 1
    assert (
        Reservation.objects.filter(item=item, status=Reservation.Status.ACTIVE).count()
        == 1
    )
    assert (
        BackupMiner.objects.filter(
            item=item, status=BackupMiner.Status.PROMOTED
        ).count()
        == 1
    )


@pytest.mark.django_db(transaction=True)
def test_concurrent_expiry_uses_separate_connections_and_runs_once():
    item = InventoryItemFactory(mining_code="101", stock_qty=1)
    with patch("invoicing.tasks.send_invoice.delay"):
        reservation = _mine(comment_id="racing-expiry", customer_id="expiry-owner")
        _mine(
            comment_id="racing-backup",
            customer_id="expiry-backup",
            created_time=timezone.now() + timedelta(seconds=1),
        )
    assert isinstance(reservation, Reservation)
    _due(reservation)

    def run_expiry() -> bool:
        close_old_connections()
        try:
            return expiry.expire_reservation(reservation.pk)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: run_expiry(), range(2)))

    assert sorted(outcomes) == [False, True]
    reservation.refresh_from_db()
    item.refresh_from_db()
    assert reservation.status == Reservation.Status.EXPIRED
    assert item.reserved_qty == 1
    assert item.reserved_qty >= 0
    assert (
        Reservation.objects.filter(
            item=item,
            status=Reservation.Status.ACTIVE,
        ).count()
        == 1
    )
    assert (
        BackupMiner.objects.filter(
            item=item,
            status=BackupMiner.Status.PROMOTED,
        ).count()
        == 1
    )


@pytest.mark.django_db(transaction=True)
def test_expiry_holds_item_row_lock_against_an_independent_connection():
    InventoryItemFactory(mining_code="101", stock_qty=1)
    with patch("invoicing.tasks.send_invoice.delay"):
        reservation = _mine(comment_id="lock-holder", customer_id="lock-owner")
    assert isinstance(reservation, Reservation)
    _due(reservation)
    original_release = expiry.release
    lock_was_held = []

    def probe_lock_then_release(locked_item, qty, *, actor):
        def try_nowait_lock() -> bool:
            close_old_connections()
            try:
                with transaction.atomic():
                    InventoryItem.objects.select_for_update(nowait=True).get(
                        pk=locked_item.pk
                    )
                return False
            except DatabaseError:
                return True
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as executor:
            lock_was_held.append(executor.submit(try_nowait_lock).result(timeout=10))
        return original_release(locked_item, qty, actor=actor)

    with patch.object(expiry, "release", side_effect=probe_lock_then_release):
        assert expiry.expire_reservation(reservation.pk) is True
    assert lock_was_held == [True]


@pytest.mark.django_db(transaction=True)
def test_concurrent_override_and_expiry_end_in_one_consistent_state():
    item = InventoryItemFactory(mining_code="101", stock_qty=1)
    new_customer = CustomerFactory()
    with patch("invoicing.tasks.send_invoice.delay"):
        reservation = _mine(comment_id="racing-holder", customer_id="old-owner")
    assert isinstance(reservation, Reservation)
    _due(reservation)
    actor = UserFactory(role="SUPER_ADMIN")
    barrier = Barrier(2)

    def run_expiry() -> str:
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return (
                "expired"
                if expiry.expire_reservation(reservation.pk)
                else "not-expired"
            )
        finally:
            connections.close_all()

    def run_override() -> str:
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            override_reservation(
                reservation.pk,
                new_customer_id=new_customer.pk,
                reason="concurrency test",
                actor=actor,
            )
            return "overridden"
        except ReservationConflict:
            return "conflict"
        finally:
            connections.close_all()

    with patch("mining.services.enqueue_invoice_after_commit"):
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda fn: fn(), [run_expiry, run_override]))

    reservation.refresh_from_db()
    item.refresh_from_db()
    if reservation.status == Reservation.Status.EXPIRED:
        assert sorted(outcomes) == ["conflict", "expired"]
        assert item.reserved_qty == 0
    else:
        assert reservation.status == Reservation.Status.OVERRIDDEN
        assert sorted(outcomes) == ["not-expired", "overridden"]
        assert item.reserved_qty == 1
        assert (
            Reservation.objects.filter(
                item=item,
                customer=new_customer,
                status=Reservation.Status.ACTIVE,
            ).count()
            == 1
        )


def test_confirmed_reservation_is_never_expired():
    item = InventoryItemFactory()
    reservation = ReservationFactory(
        item=item,
        status=Reservation.Status.CONFIRMED,
        payment_deadline=timezone.now() - timedelta(minutes=1),
    )

    assert expiry.expire_reservation(reservation.pk) is False
    reservation.refresh_from_db()
    assert reservation.status == Reservation.Status.CONFIRMED


def test_paid_order_recheck_prevents_expiry(monkeypatch):
    item = InventoryItemFactory(mining_code="101", stock_qty=1)
    with patch("invoicing.tasks.send_invoice.delay"):
        reservation = _mine(comment_id="holder", customer_id="holder")
    order = Order.objects.get(customer__facebook_id="holder")
    _due(reservation)
    original_lock = expiry.lock_item

    def pay_order_before_lock(inventory_item):
        Order.objects.filter(pk=order.pk).update(status=Order.Status.PAID)
        return original_lock(inventory_item)

    monkeypatch.setattr(expiry, "lock_item", pay_order_before_lock)

    assert expiry.expire_reservation(reservation.pk) is False
    reservation.refresh_from_db()
    item.refresh_from_db()
    assert reservation.status == Reservation.Status.ACTIVE
    assert item.reserved_qty == 1


def test_promotion_drops_customer_already_holding_item_and_tries_next():
    item = InventoryItemFactory(mining_code="101", stock_qty=2)
    with patch("invoicing.tasks.send_invoice.delay"):
        expiring = _mine(comment_id="expiring", customer_id="owner-1")
        second_holder = _mine(
            comment_id="second-holder",
            customer_id="owner-2",
            created_time=timezone.now() + timedelta(seconds=1),
        )
    duplicate_comment = LiveCommentFactory(
        fb_commenter_id="owner-2",
        outcome=LiveComment.Outcome.BACKUP,
        created_time=timezone.now() + timedelta(seconds=2),
    )
    duplicate_backup = BackupMinerFactory(
        item=item,
        customer=second_holder.customer,
        comment=duplicate_comment,
    )
    next_customer = CustomerFactory()
    next_comment = LiveCommentFactory(
        fb_commenter_id=next_customer.facebook_id,
        outcome=LiveComment.Outcome.BACKUP,
        created_time=timezone.now() + timedelta(seconds=3),
    )
    next_backup = BackupMinerFactory(
        item=item,
        customer=next_customer,
        comment=next_comment,
        priority=2,
    )
    _due(expiring)

    assert expiry.expire_reservation(expiring.pk) is True
    duplicate_backup.refresh_from_db()
    next_backup.refresh_from_db()
    assert duplicate_backup.status == BackupMiner.Status.DROPPED
    assert next_backup.status == BackupMiner.Status.PROMOTED
    assert Reservation.objects.filter(
        item=item,
        customer=next_customer,
        status=Reservation.Status.ACTIVE,
    ).exists()
    assert second_holder.status == Reservation.Status.ACTIVE


def test_no_backup_releases_item_to_available():
    item = InventoryItemFactory(mining_code="101", stock_qty=1)
    with patch("invoicing.tasks.send_invoice.delay"):
        reservation = _mine(comment_id="only", customer_id="only-buyer")
    _due(reservation)

    assert expiry.expire_reservation(reservation.pk) is True
    item.refresh_from_db()
    assert item.reserved_qty == 0
    assert item.availability_status == InventoryItem.AvailabilityStatus.AVAILABLE


def test_empty_order_keeps_zero_total_and_expires_sent_invoice():
    InventoryItemFactory(mining_code="101", stock_qty=1)
    with patch("invoicing.tasks.send_invoice.delay"):
        reservation = _mine(comment_id="only", customer_id="only-buyer")
    order = Order.objects.get(customer__facebook_id="only-buyer")
    invoice = InvoiceFactory(
        order=order,
        total=order.total_amount,
        status=Invoice.Status.SENT,
        sent_at=timezone.now(),
    )
    _due(reservation)

    assert expiry.expire_reservation(reservation.pk) is True
    order.refresh_from_db()
    invoice.refresh_from_db()
    assert order.status == Order.Status.UNPAID
    assert order.total_amount == Decimal("0.00")
    assert invoice.status == Invoice.Status.EXPIRED


def test_expiry_with_remaining_items_recomputes_and_enqueues_invoice_on_commit(
    django_capture_on_commit_callbacks,
):
    first = InventoryItemFactory(mining_code="101", stock_qty=1)
    second = InventoryItemFactory(mining_code="102", stock_qty=1)
    with patch("invoicing.tasks.send_invoice.delay"):
        reservation = _mine(comment_id="expire", customer_id="buyer", code="101")
    order = Order.objects.get(customer__facebook_id="buyer")
    OrderItemFactory(
        order=order,
        item=second,
        quantity=2,
        unit_price=Decimal("7.50"),
    )
    order.total_amount += Decimal("15.00")
    order.save(update_fields=["total_amount"])
    InvoiceFactory(order=order, total=order.total_amount)
    _due(reservation)
    callback = patch("mining.expiry.enqueue_invoice_after_commit")
    with callback as enqueue, django_capture_on_commit_callbacks(execute=True):
        assert expiry.expire_reservation(reservation.pk) is True

    order.refresh_from_db()
    assert order.total_amount == Decimal("15.00")
    enqueue.assert_called_once()
    assert enqueue.call_args.args == (order.pk,)
    assert enqueue.call_args.kwargs == {
        "reason": "reservation expired or cancelled",
        "caller": "_close_reservation_locked",
    }
    first.refresh_from_db()
    assert first.reserved_qty == 0


@pytest.mark.django_db(transaction=True)
def test_expiry_enqueues_old_and_promoted_orders_only_after_commit():
    first_item = InventoryItemFactory(mining_code="101", stock_qty=1)
    remaining_item = InventoryItemFactory(mining_code="102", stock_qty=1)
    with patch("invoicing.tasks.send_invoice.delay"):
        reservation = _mine(comment_id="commit-holder", customer_id="commit-owner")
        _mine(
            comment_id="commit-backup",
            customer_id="commit-next",
            created_time=timezone.now() + timedelta(seconds=1),
        )
    old_order = Order.objects.get(customer__facebook_id="commit-owner")
    OrderItemFactory(
        order=old_order,
        item=remaining_item,
        quantity=1,
        unit_price=Decimal("9.00"),
    )
    old_order.total_amount += Decimal("9.00")
    old_order.save(update_fields=["total_amount"])
    InvoiceFactory(order=old_order, total=old_order.total_amount)
    _due(reservation)

    with patch("mining.expiry.enqueue_invoice_after_commit") as enqueue:

        def enqueue_without_locks(order_id, **kwargs):
            assert connection.in_atomic_block is False

        enqueue.side_effect = enqueue_without_locks
        with transaction.atomic():
            assert expiry.expire_reservation(reservation.pk) is True
            enqueue.assert_not_called()
        assert enqueue.call_count == 2
        assert {entry.args[0] for entry in enqueue.call_args_list} == {
            old_order.pk,
            Order.objects.get(customer__facebook_id="commit-next").pk,
        }
    first_item.refresh_from_db()
    assert first_item.reserved_qty == 1


def test_expiry_batch_continues_after_one_reservation_failure(monkeypatch, caplog):
    first = ReservationFactory(payment_deadline=timezone.now() - timedelta(seconds=1))
    second = ReservationFactory(payment_deadline=timezone.now() - timedelta(seconds=1))
    calls = []

    def fail_then_expire(reservation_id, *, now):
        calls.append(reservation_id)
        if reservation_id == first.pk:
            raise RuntimeError("forced failure")
        return True

    monkeypatch.setattr(expiry, "expire_reservation", fail_then_expire)
    result = expiry.expire_due_reservations()

    assert result == {"candidates": 2, "expired": 1, "errors": 1}
    assert calls == [first.pk, second.pk]
    assert f"Could not expire reservation {first.pk}" in caplog.text


@pytest.mark.django_db
def test_expiry_retries_postgres_deadlock_before_reporting_failure(monkeypatch, caplog):
    reservation = ReservationFactory(
        payment_deadline=timezone.now() - timedelta(seconds=1)
    )
    deadlock = OperationalError("deadlock detected")

    class PostgresDeadlock(Exception):
        sqlstate = "40P01"

    deadlock.__cause__ = PostgresDeadlock("deadlock detected")
    attempts = []

    def fail_once_then_expire(reservation_id, *, now):
        attempts.append(reservation_id)
        if len(attempts) == 1:
            raise deadlock
        return True

    monkeypatch.setattr(expiry, "_expire_reservation_once", fail_once_then_expire)
    monkeypatch.setattr(expiry, "sleep", lambda _seconds: None)

    with caplog.at_level("WARNING", logger="mining.expiry"):
        result = expiry.expire_due_reservations()

    assert result == {"candidates": 1, "expired": 1, "errors": 0}
    assert attempts == [reservation.pk, reservation.pk]
    assert "Retrying expiry for reservation" in caplog.text


def test_beat_schedule_targets_expiry_task():
    from django.conf import settings

    entry = settings.CELERY_BEAT_SCHEDULE["expire-mining-reservations-every-60-seconds"]
    assert entry["task"] == "mining.expire_reservations"
    assert entry["schedule"] == 60.0
