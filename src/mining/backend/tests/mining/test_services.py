from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from typing import cast
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection, connections, transaction
from django.utils import timezone

from accounts.models import Customer
from activitylog.models import ActivityLog
from inventory.models import InventoryItem
from inventory.services import InvalidInventoryState
from invoicing.tasks import send_invoice
from mining.models import BackupMiner, LiveComment, Reservation
from mining.services import (
    BackupPosition,
    CommentData,
    InvalidMine,
    confirm_comment,
    normalize_message,
    parse_code,
    process_comment,
)
from orders.models import Order, OrderItem
from tests.factories import (
    CustomerFactory,
    InventoryItemFactory,
    LiveCommentFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


def comment_data(
    *,
    comment_id: str = "comment-1",
    code: str = "101",
    user_id: str = "facebook-1",
    name: str = "Juan",
    created_time: datetime | None = None,
    message: str | None = None,
) -> CommentData:
    """Build normalized source fields for a detector test."""
    return {
        "fb_comment_id": comment_id,
        "live_video_id": "live-1",
        "message": message if message is not None else f"mine {code}",
        "fb_commenter_id": user_id,
        "fb_commenter_name": name,
        "created_time": created_time or timezone.now(),
    }


def test_first_comment_wins_and_second_comment_is_backup():
    item = InventoryItemFactory(mining_code="101", stock_qty=1)
    first = process_comment(comment_data(comment_id="1", user_id="buyer-1"))
    second = process_comment(
        comment_data(
            comment_id="2",
            user_id="buyer-2",
            created_time=timezone.now() + timedelta(seconds=1),
        )
    )

    assert isinstance(first, Reservation)
    assert isinstance(second, BackupPosition)
    assert second.priority == 1
    assert Reservation.objects.filter(item=item).count() == 1
    assert (
        BackupMiner.objects.filter(item=item, status=BackupMiner.Status.WAITING).count()
        == 1
    )


def test_same_timestamp_tie_uses_comment_id_and_late_holder_is_not_displaced():
    InventoryItemFactory(mining_code="101", stock_qty=1)
    created_time = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    # The higher id arrives first and becomes holder. The lower id is
    # deterministically earlier, but the explicit late-arrival rule preserves
    # the current owner and flags it for review.
    holder = process_comment(
        comment_data(comment_id="z-comment", created_time=created_time)
    )
    earlier = process_comment(
        comment_data(
            comment_id="a-comment",
            user_id="facebook-2",
            created_time=created_time,
        )
    )

    assert isinstance(holder, Reservation)
    assert isinstance(earlier, InvalidMine)
    assert earlier.outcome == LiveComment.Outcome.FLAGGED_LATE
    holder.refresh_from_db()
    assert holder.customer.facebook_id == "facebook-1"
    assert ActivityLog.objects.filter(
        action="MINE_FLAGGED_LATE",
        new_value__holder_comment_id="z-comment",
        new_value__incoming_comment_id="a-comment",
    ).exists()


def test_earlier_original_time_arriving_later_wins_while_item_is_free():
    InventoryItemFactory(mining_code="101", stock_qty=1)
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    result = process_comment(
        comment_data(
            comment_id="earlier",
            user_id="early-buyer",
            created_time=now,
        )
    )

    assert isinstance(result, Reservation)
    assert result.customer.facebook_id == "early-buyer"


def test_duplicate_comment_id_is_idempotent():
    InventoryItemFactory(mining_code="101", stock_qty=1)
    data = comment_data(comment_id="same-id")

    first = process_comment(data)
    second = process_comment(data)

    assert isinstance(first, Reservation)
    assert isinstance(second, Reservation)
    assert first.pk == second.pk
    assert LiveComment.objects.filter(fb_comment_id="same-id").count() == 1
    assert Reservation.objects.count() == 1
    assert OrderItem.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_duplicate_comment_race_is_handled_once(monkeypatch):
    InventoryItemFactory(mining_code="101", stock_qty=1)
    barrier = Barrier(2)
    manager = LiveComment.objects
    original_create = manager.create

    def synchronized_create(*args, **kwargs):
        barrier.wait(timeout=10)
        return original_create(*args, **kwargs)

    monkeypatch.setattr(manager, "create", synchronized_create)
    data = comment_data(comment_id="racing-id")

    def process_in_thread() -> object:
        close_old_connections()
        try:
            return process_comment(data)
        finally:
            connections.close_all()

    with patch.object(send_invoice, "delay") as send:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: process_in_thread(), range(2)))

    assert all(isinstance(result, Reservation) for result in results)
    assert len({cast(Reservation, result).pk for result in results}) == 1
    assert LiveComment.objects.filter(fb_comment_id="racing-id").count() == 1
    assert Reservation.objects.count() == 1
    assert OrderItem.objects.count() == 1
    send.assert_called_once()


@pytest.mark.django_db(transaction=True)
def test_concurrent_mines_on_last_unit_create_one_reservation_and_backups(monkeypatch):
    item = InventoryItemFactory(mining_code="101", stock_qty=1)
    base_time = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    entries = [
        comment_data(
            comment_id=f"comment-{index:02}",
            user_id=f"buyer-{index}",
            created_time=base_time + timedelta(seconds=index),
        )
        for index in range(10)
    ]
    barrier = Barrier(len(entries))
    from mining import services as mining_services

    original_lock_item = mining_services.lock_item

    def synchronized_lock(item):
        barrier.wait(timeout=10)
        return original_lock_item(item)

    monkeypatch.setattr(mining_services, "lock_item", synchronized_lock)

    def process_in_thread(index: int) -> object:
        close_old_connections()
        try:
            return process_comment(entries[index])
        finally:
            connections.close_all()

    with patch.object(send_invoice, "delay"):
        with ThreadPoolExecutor(max_workers=len(entries)) as executor:
            results = list(executor.map(process_in_thread, range(len(entries))))

    item.refresh_from_db()
    assert sum(isinstance(result, Reservation) for result in results) == 1
    non_winners = [result for result in results if not isinstance(result, Reservation)]
    assert len(non_winners) == 9
    assert all(
        isinstance(result, BackupPosition)
        or (
            isinstance(result, InvalidMine)
            and result.outcome == LiveComment.Outcome.FLAGGED_LATE
        )
        for result in non_winners
    )
    assert Reservation.objects.filter(item=item).count() == 1
    backup_count = BackupMiner.objects.filter(
        item=item, status=BackupMiner.Status.WAITING
    ).count()
    assert backup_count == 9
    assert item.stock_qty == 1
    assert item.reserved_qty == 1
    assert item.reserved_qty <= item.stock_qty


def test_late_earlier_comment_flags_without_changing_holder():
    item = InventoryItemFactory(mining_code="101", stock_qty=2)
    base_time = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    holder = process_comment(
        comment_data(comment_id="later", created_time=base_time + timedelta(seconds=5))
    )
    late = process_comment(
        comment_data(
            comment_id="earlier",
            user_id="early-buyer",
            created_time=base_time,
        )
    )

    assert isinstance(holder, Reservation)
    assert isinstance(late, InvalidMine)
    assert late.outcome == LiveComment.Outcome.FLAGGED_LATE
    assert Reservation.objects.filter(item=item).count() == 1
    assert not Reservation.objects.filter(customer__facebook_id="early-buyer").exists()
    backup = BackupMiner.objects.get(customer__facebook_id="early-buyer")
    assert backup.comment == late.comment
    assert backup.status == BackupMiner.Status.WAITING
    assert ActivityLog.objects.filter(action="MINE_FLAGGED_LATE").exists()


def test_flagged_late_authors_are_ranked_in_backup_order():
    item = InventoryItemFactory(mining_code="101", stock_qty=1)
    base_time = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    holder = process_comment(
        comment_data(comment_id="holder", created_time=base_time + timedelta(seconds=5))
    )
    later_backup = process_comment(
        comment_data(
            comment_id="later-backup",
            user_id="later-buyer",
            created_time=base_time + timedelta(seconds=6),
        )
    )
    earlier_flagged = process_comment(
        comment_data(
            comment_id="earlier-flagged",
            user_id="early-buyer",
            created_time=base_time,
        )
    )

    assert isinstance(holder, Reservation)
    assert isinstance(later_backup, BackupPosition)
    assert isinstance(earlier_flagged, InvalidMine)
    assert earlier_flagged.outcome == LiveComment.Outcome.FLAGGED_LATE
    backups = list(
        BackupMiner.objects.filter(item=item, status=BackupMiner.Status.WAITING)
        .select_related("comment")
        .order_by("priority")
    )
    assert [backup.comment.fb_comment_id for backup in backups] == [
        "earlier-flagged",
        "later-backup",
    ]
    assert [backup.priority for backup in backups] == [1, 2]


def test_duplicate_mine_after_flagged_late_does_not_add_another_backup():
    InventoryItemFactory(mining_code="101", stock_qty=2)
    base_time = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    process_comment(
        comment_data(comment_id="holder", created_time=base_time + timedelta(seconds=5))
    )
    flagged = process_comment(
        comment_data(
            comment_id="first-early",
            user_id="early-buyer",
            created_time=base_time,
        )
    )
    duplicate = process_comment(
        comment_data(
            comment_id="second-early",
            user_id="early-buyer",
            created_time=base_time + timedelta(seconds=1),
        )
    )

    assert isinstance(flagged, InvalidMine)
    assert flagged.outcome == LiveComment.Outcome.FLAGGED_LATE
    assert isinstance(duplicate, InvalidMine)
    assert duplicate.outcome == LiveComment.Outcome.IGNORED
    assert (
        BackupMiner.objects.filter(
            customer__facebook_id="early-buyer",
            item__mining_code="101",
            status=BackupMiner.Status.WAITING,
        ).count()
        == 1
    )


@pytest.mark.parametrize(
    ("message", "outcome", "parsed_code"),
    [
        ("unknown", LiveComment.Outcome.INVALID, "unknown"),
        ("I want 2 of those", LiveComment.Outcome.IGNORED, None),
        ("mine", LiveComment.Outcome.IGNORED, None),
    ],
)
def test_invalid_and_non_mine_comments_are_recorded(message, outcome, parsed_code):
    InventoryItemFactory(mining_code="101", stock_qty=1)

    result = process_comment(
        comment_data(comment_id=f"message-{message}", message=message)
    )

    assert isinstance(result, InvalidMine)
    assert result.outcome == outcome
    stored = LiveComment.objects.get(fb_comment_id=f"message-{message}")
    assert stored.parsed_code == parsed_code


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Mine 101", "101"),
        ("MINE101", "101"),
        ("  mine   101  ", "101"),
        ("101", "101"),
    ],
)
def test_mining_pattern_variants(message, expected):
    assert parse_code(normalize_message(message)) == expected


def test_backup_priority_uses_original_time_and_logs_rank_changes():
    item = InventoryItemFactory(mining_code="101", stock_qty=1, reserved_qty=1)
    item.availability_status = InventoryItem.AvailabilityStatus.RESERVED
    item.save()
    base_time = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    late_backup = process_comment(
        comment_data(
            comment_id="c",
            user_id="buyer-c",
            created_time=base_time + timedelta(seconds=20),
        )
    )
    early_backup = process_comment(
        comment_data(
            comment_id="a",
            user_id="buyer-a",
            created_time=base_time,
        )
    )
    middle_backup = process_comment(
        comment_data(
            comment_id="b",
            user_id="buyer-b",
            created_time=base_time + timedelta(seconds=10),
        )
    )

    assert isinstance(late_backup, BackupPosition)
    assert isinstance(early_backup, BackupPosition)
    assert isinstance(middle_backup, BackupPosition)
    assert list(
        BackupMiner.objects.filter(item=item).values_list(
            "comment__fb_comment_id", flat=True
        )
    ) == ["a", "b", "c"]
    assert list(
        BackupMiner.objects.filter(item=item)
        .order_by("priority")
        .values_list("priority", flat=True)
    ) == [1, 2, 3]
    assert ActivityLog.objects.filter(action="BACKUP_PRIORITY_UPDATED").exists()


def test_duplicate_customer_mine_is_ignored_and_logged():
    InventoryItemFactory(mining_code="101", stock_qty=2)
    first = process_comment(comment_data(comment_id="first", user_id="same-buyer"))
    duplicate = process_comment(
        comment_data(
            comment_id="second",
            user_id="same-buyer",
            created_time=timezone.now() + timedelta(seconds=1),
        )
    )

    assert isinstance(first, Reservation)
    assert isinstance(duplicate, InvalidMine)
    assert duplicate.outcome == LiveComment.Outcome.IGNORED
    assert Reservation.objects.count() == 1
    assert ActivityLog.objects.filter(action="MINE_IGNORED").exists()


def test_second_item_merges_into_unpaid_order_and_keeps_price_snapshot():
    first_item = InventoryItemFactory(mining_code="101", selling_price=Decimal("10.25"))
    second_item = InventoryItemFactory(
        mining_code="202", selling_price=Decimal("20.50")
    )
    first = process_comment(
        comment_data(comment_id="first", user_id="same-buyer", code="101")
    )
    first_item.selling_price = Decimal("99.99")
    first_item.save(update_fields=["selling_price"])
    second = process_comment(
        comment_data(
            comment_id="second",
            user_id="same-buyer",
            code="202",
            created_time=timezone.now() + timedelta(seconds=1),
        )
    )

    assert isinstance(first, Reservation)
    assert isinstance(second, Reservation)
    order = Order.objects.get(
        customer__facebook_id="same-buyer", status=Order.Status.UNPAID
    )
    lines = {line.item_id: line for line in order.items.all()}
    assert len(lines) == 2
    assert lines[first_item.pk].unit_price == Decimal("10.25")
    assert lines[second_item.pk].unit_price == Decimal("20.50")
    assert order.total_amount == Decimal("30.75")
    assert isinstance(order.total_amount, Decimal)


def test_customer_with_paid_order_gets_a_new_unpaid_order():
    InventoryItemFactory(mining_code="101")
    customer = CustomerFactory(facebook_id="paid-buyer")
    Order.objects.create(
        customer=customer,
        status=Order.Status.PAID,
        total_amount=Decimal("5.00"),
    )

    result = process_comment(comment_data(user_id="paid-buyer"))

    assert isinstance(result, Reservation)
    assert (
        Order.objects.filter(customer=customer, status=Order.Status.PAID).count() == 1
    )
    assert (
        Order.objects.filter(customer=customer, status=Order.Status.UNPAID).count() == 1
    )


def test_existing_customer_name_is_updated_only_when_blank():
    InventoryItemFactory(mining_code="101")
    customer = CustomerFactory(facebook_id="named-buyer", full_name="")

    process_comment(comment_data(user_id="named-buyer", name="New Facebook Name"))

    customer.refresh_from_db()
    assert customer.full_name == "New Facebook Name"
    assert ActivityLog.objects.filter(action="CUSTOMER_UPDATED").exists()


def test_transaction_failure_after_reserve_rolls_back_all_mining_changes(monkeypatch):
    item = InventoryItemFactory(mining_code="101", stock_qty=1)

    def reserve_then_fail(*args, **kwargs):
        from inventory.services import reserve as real_reserve

        real_reserve(*args, **kwargs)
        raise RuntimeError("forced post-reservation failure")

    monkeypatch.setattr("mining.services.reserve", reserve_then_fail)

    with pytest.raises(RuntimeError, match="forced post-reservation failure"):
        process_comment(comment_data(comment_id="rollback"))

    item.refresh_from_db()
    assert item.reserved_qty == 0
    assert LiveComment.objects.filter(fb_comment_id="rollback").count() == 0
    assert Reservation.objects.count() == 0
    assert Customer.objects.filter(facebook_id="facebook-1").count() == 0
    assert Order.objects.count() == 0
    assert OrderItem.objects.count() == 0
    assert ActivityLog.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_invoice_task_is_enqueued_once_after_successful_commit():
    InventoryItemFactory(mining_code="101", stock_qty=1)

    def enqueue(order_id):
        assert connection.in_atomic_block is False

    with patch.object(send_invoice, "delay") as send:
        send.side_effect = enqueue
        with transaction.atomic():
            result = process_comment(comment_data(comment_id="invoice"))
            assert isinstance(result, Reservation)
            send.assert_not_called()

        send.assert_called_once()
        send.assert_called_once_with(Order.objects.get().pk)


@pytest.mark.django_db(transaction=True)
def test_manual_confirmation_enqueues_after_commit_without_locks():
    InventoryItemFactory(mining_code="101", stock_qty=1)
    comment = LiveCommentFactory(
        fb_comment_id="manual-confirm",
        outcome=LiveComment.Outcome.IGNORED,
        parsed_code=None,
    )
    actor = UserFactory()

    def enqueue(order_id, **kwargs):
        assert connection.in_atomic_block is False

    with patch("mining.services.enqueue_invoice_after_commit") as enqueue_task:
        enqueue_task.side_effect = enqueue
        with transaction.atomic():
            result = confirm_comment(
                comment.pk,
                mining_code="101",
                actor=actor,
            )
            assert isinstance(result, Reservation)
            enqueue_task.assert_not_called()

        enqueue_task.assert_called_once()
        assert enqueue_task.call_args.args[0] == Order.objects.get().pk


@pytest.mark.parametrize(
    "message",
    ["mine", "not a mine", "mine 999"],
)
def test_non_reservation_results_do_not_enqueue_invoice(message):
    InventoryItemFactory(mining_code="101", stock_qty=1)
    with patch.object(send_invoice, "delay") as send:
        with transaction.atomic():
            result = process_comment(
                comment_data(comment_id=f"not-reserved-{message}", message=message)
            )
            send.assert_not_called()

    assert isinstance(result, (BackupPosition, InvalidMine))
    send.assert_not_called()


def test_backup_does_not_enqueue_invoice():
    item = InventoryItemFactory(
        mining_code="101",
        stock_qty=1,
        reserved_qty=1,
        availability_status=InventoryItem.AvailabilityStatus.RESERVED,
    )
    assert item.stock_qty == item.reserved_qty

    with patch.object(send_invoice, "delay") as send:
        result = process_comment(comment_data(comment_id="backup"))

    assert isinstance(result, BackupPosition)
    send.assert_not_called()


def test_float_money_values_are_rejected_by_inventory_management():
    from inventory.services import manage_inventory

    with pytest.raises(InvalidInventoryState, match="Decimal-compatible"):
        manage_inventory(
            action="create",
            mining_code="FLOAT-PRICE",
            name="No floats",
            selling_price=1.25,
            acquisition_cost=Decimal("1.00"),
        )
