from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Customer, User
from activitylog.models import ActivityLog
from inventory.models import InventoryItem
from invoicing.tasks import send_invoice
from mining.models import BackupMiner, LiveComment, Reservation
from mining.services import override_reservation, process_comment
from orders.models import Order, OrderItem
from tests.factories import (
    CustomerFactory,
    InventoryItemFactory,
    LiveCommentFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


def _process(
    *,
    comment_id: str,
    customer_id: str,
    message: str = "mine 101",
):
    return process_comment(
        {
            "fb_comment_id": comment_id,
            "live_video_id": "live-1",
            "message": message,
            "fb_commenter_id": customer_id,
            "fb_commenter_name": customer_id,
            "created_time": timezone.now(),
        }
    )


def _client(role: str) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=UserFactory(role=role))
    return client


def _reservation(*, customer_id: str = "owner", code: str = "101"):
    InventoryItemFactory(mining_code=code, stock_qty=1, selling_price=Decimal("50.00"))
    with patch.object(send_invoice, "delay"):
        reservation = process_comment(
            {
                "fb_comment_id": f"mine-{customer_id}",
                "live_video_id": "live-1",
                "message": f"mine {code}",
                "fb_commenter_id": customer_id,
                "fb_commenter_name": customer_id,
                "created_time": timezone.now(),
            }
        )
    assert isinstance(reservation, Reservation)
    return reservation


def test_reservation_list_paginates_and_filters_by_status_code_and_customer():
    first = _reservation(customer_id="buyer-one")
    second = _reservation(customer_id="buyer-two", code="102")
    user = Customer.objects.get(facebook_id="buyer-one")
    client = _client(User.Role.LIVESELLER_ADMIN)

    response = client.get(
        "/api/reservations/?status=ACTIVE&item_code=101&customer=" + str(user.pk)
    )

    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == first.pk
    assert response.data["results"][0]["customer_id"] == user.pk
    assert second.pk != first.pk


def test_patch_extends_deadline_and_logs_actor_previous_and_new():
    reservation = _reservation()
    actor = UserFactory(role=User.Role.LIVESELLER_ADMIN)
    new_deadline = timezone.now() + timedelta(days=2)
    client = APIClient()
    client.force_authenticate(actor)

    response = client.patch(
        f"/api/reservations/{reservation.pk}/",
        {"payment_deadline": new_deadline.isoformat()},
        format="json",
    )

    assert response.status_code == 200
    reservation.refresh_from_db()
    assert reservation.payment_deadline == new_deadline
    log = ActivityLog.objects.get(
        action="RESERVATION_UPDATED",
        target_type="Reservation",
        target_id=str(reservation.pk),
    )
    assert log.user == actor
    assert "payment_deadline" in log.previous_value
    assert "payment_deadline" in log.new_value


def test_confirmed_reservation_is_not_expired_by_lifecycle_task():
    reservation = _reservation()
    client = _client(User.Role.LIVESELLER_ADMIN)

    response = client.patch(
        f"/api/reservations/{reservation.pk}/",
        {"status": Reservation.Status.CONFIRMED},
        format="json",
    )
    assert response.status_code == 200
    reservation.refresh_from_db()
    reservation.payment_deadline = timezone.now() - timedelta(minutes=1)
    reservation.save(update_fields=["payment_deadline"])

    from mining.expiry import expire_reservation

    assert expire_reservation(reservation.pk) is False
    reservation.refresh_from_db()
    assert reservation.status == Reservation.Status.CONFIRMED


def test_cancel_releases_stock_and_promotes_next_backup():
    InventoryItemFactory(mining_code="101", stock_qty=1)
    with patch.object(send_invoice, "delay"):
        first = _process(comment_id="first", customer_id="first")
        backup = _process(
            comment_id="backup",
            customer_id="backup",
            message="mine 101",
        )
    assert isinstance(first, Reservation)
    assert backup.outcome == LiveComment.Outcome.BACKUP
    old_line_id = OrderItem.objects.get(
        order__customer=first.customer, item=first.item
    ).pk

    response = _client(User.Role.LIVESELLER_ADMIN).patch(
        f"/api/reservations/{first.pk}/",
        {"status": Reservation.Status.CANCELLED},
        format="json",
    )

    assert response.status_code == 200
    first.refresh_from_db()
    item = InventoryItem.objects.get(mining_code="101")
    promoted = Reservation.objects.get(customer__facebook_id="backup")
    assert first.status == Reservation.Status.CANCELLED
    assert promoted.status == Reservation.Status.ACTIVE
    assert item.reserved_qty == 1
    assert (
        BackupMiner.objects.get(comment=backup.comment).status
        == BackupMiner.Status.PROMOTED
    )
    assert ActivityLog.objects.filter(
        action="ORDER_ITEM_UPDATED",
        target_type="OrderItem",
        target_id=str(old_line_id),
        reason="Reservation released.",
    ).exists()


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        ({"status": Reservation.Status.CONFIRMED}, 409),
        ({"payment_deadline": "2000-01-01T00:00:00Z"}, 400),
        ({"status": "EXPIRED"}, 400),
        ({"unknown": "value"}, 400),
    ],
)
def test_patch_rejects_illegal_or_unknown_changes(body, expected_status):
    reservation = _reservation()
    if body.get("status") == Reservation.Status.CONFIRMED:
        reservation.status = Reservation.Status.EXPIRED
        reservation.save(update_fields=["status"])

    response = _client(User.Role.LIVESELLER_ADMIN).patch(
        f"/api/reservations/{reservation.pk}/", body, format="json"
    )
    assert response.status_code == expected_status


def test_manual_confirm_ignored_comment_with_code_reserves():
    InventoryItemFactory(mining_code="101", stock_qty=1)
    comment = LiveCommentFactory(
        fb_comment_id="ignored-comment",
        fb_commenter_id="manual-buyer",
        outcome=LiveComment.Outcome.IGNORED,
        parsed_code=None,
    )

    response = _client(User.Role.LIVESELLER_ADMIN).post(
        f"/api/mining/mines/{comment.pk}/confirm/",
        {"mining_code": "101"},
        format="json",
    )

    assert response.status_code == 200
    comment.refresh_from_db()
    assert response.data["outcome"] == LiveComment.Outcome.RESERVED
    assert comment.parsed_code == "101"
    assert comment.reservation_id is not None


def test_manual_confirm_taken_item_adds_backup():
    InventoryItemFactory(mining_code="101", stock_qty=1)
    with patch.object(send_invoice, "delay"):
        _process(comment_id="first", customer_id="first")
    comment = LiveCommentFactory(
        fb_comment_id="invalid-comment",
        fb_commenter_id="second",
        outcome=LiveComment.Outcome.INVALID,
        parsed_code="101",
    )

    response = _client(User.Role.LIVESELLER_ADMIN).post(
        f"/api/mining/mines/{comment.pk}/confirm/", {}, format="json"
    )

    assert response.status_code == 200
    assert response.data["outcome"] == LiveComment.Outcome.BACKUP
    assert BackupMiner.objects.filter(comment=comment).exists()


@pytest.mark.parametrize(
    ("outcome", "body", "expected"),
    [
        (LiveComment.Outcome.FLAGGED_LATE, {"mining_code": "101"}, 409),
        (LiveComment.Outcome.IGNORED, {}, 400),
        (LiveComment.Outcome.RESERVED, {"mining_code": "101"}, 409),
    ],
)
def test_manual_confirm_rejects_not_allowed_or_missing_code(outcome, body, expected):
    InventoryItemFactory(mining_code="101", stock_qty=1)
    comment = LiveCommentFactory(
        outcome=outcome,
        parsed_code=None,
    )

    response = _client(User.Role.LIVESELLER_ADMIN).post(
        f"/api/mining/mines/{comment.pk}/confirm/",
        body,
        format="json",
    )

    assert response.status_code == expected


@pytest.mark.django_db(transaction=True)
def test_override_moves_reservation_order_line_and_preserves_price():
    reservation = _reservation()
    item = reservation.item
    old_customer = reservation.customer
    new_customer = CustomerFactory()
    old_order = Order.objects.get(customer=old_customer)
    old_line = OrderItem.objects.get(order=old_order, item=item)
    old_line_id = old_line.pk
    old_line.unit_price = Decimal("37.25")
    old_line.save(update_fields=["unit_price"])
    old_order.total_amount = Decimal("37.25")
    old_order.save(update_fields=["total_amount"])
    reserved_before = InventoryItem.objects.get(pk=item.pk).reserved_qty
    actor = UserFactory(role=User.Role.SUPER_ADMIN)

    def enqueue(order_id, **kwargs):
        assert connection.in_atomic_block is False

    with patch("mining.services.enqueue_invoice_after_commit") as enqueue_task:
        enqueue_task.side_effect = enqueue
        result, old_order, new_order = override_reservation(
            reservation.pk,
            new_customer_id=new_customer.pk,
            reason="Customer requested transfer",
            actor=actor,
        )
        enqueue_task.assert_called_once_with(
            new_order.pk,
            reason="reservation overridden",
            caller="override_reservation",
        )

    reservation.refresh_from_db()
    item.refresh_from_db()
    old_order.refresh_from_db()
    new_order.refresh_from_db()
    result_line = OrderItem.objects.get(order=new_order, item=item)
    assert reservation.status == Reservation.Status.OVERRIDDEN
    assert result.status == Reservation.Status.ACTIVE
    assert result.customer == new_customer
    assert result_line.quantity == 1
    assert result_line.unit_price == Decimal("37.25")
    assert old_order.total_amount == Decimal("0.00")
    assert new_order.total_amount == Decimal("37.25")
    assert item.reserved_qty == reserved_before
    log = ActivityLog.objects.get(
        action="RESERVATION_OVERRIDDEN",
        target_id=str(reservation.pk),
    )
    assert log.user == actor
    assert log.reason == "Customer requested transfer"
    assert log.previous_value["old_customer_id"] == old_customer.pk
    assert log.new_value["new_customer_id"] == new_customer.pk
    assert log.new_value["new_reservation_id"] == result.pk
    assert log.previous_value["old_order_total"] == "37.25"
    assert log.previous_value["new_order_total"] == "0.00"
    assert log.new_value["old_order_total"] == "0.00"
    assert log.new_value["new_order_total"] == "37.25"
    assert ActivityLog.objects.filter(
        action="ORDER_ITEM_UPDATED",
        target_type="OrderItem",
        target_id=str(old_line_id),
        reason="Item moved by reservation override.",
    ).exists()


def test_override_into_existing_holder_returns_conflict_without_mutation():
    item = InventoryItemFactory(mining_code="101", stock_qty=2)
    with patch.object(send_invoice, "delay"):
        first = _process(comment_id="first-holder", customer_id="first-holder")
        second = _process(comment_id="second-holder", customer_id="second-holder")
    assert isinstance(first, Reservation)
    assert isinstance(second, Reservation)
    first_order = Order.objects.get(customer=first.customer)
    second_order = Order.objects.get(customer=second.customer)
    old_first_total = first_order.total_amount
    old_second_total = second_order.total_amount
    item.refresh_from_db()
    reserved_before = item.reserved_qty
    success_logs_before = ActivityLog.objects.filter(
        action="RESERVATION_OVERRIDDEN",
        target_type="Reservation",
        target_id=str(first.pk),
    ).count()

    response = _client(User.Role.SUPER_ADMIN).post(
        f"/api/reservations/{first.pk}/override/",
        {
            "new_customer_id": second.customer_id,
            "reason": "duplicate holder check",
        },
        format="json",
    )

    assert response.status_code == 409
    first.refresh_from_db()
    second.refresh_from_db()
    first_order.refresh_from_db()
    second_order.refresh_from_db()
    item.refresh_from_db()
    assert first.status == Reservation.Status.ACTIVE
    assert second.status == Reservation.Status.ACTIVE
    assert first_order.total_amount == old_first_total
    assert second_order.total_amount == old_second_total
    assert item.reserved_qty == reserved_before
    assert (
        Reservation.objects.filter(
            item=item,
            customer=second.customer,
            status__in=[Reservation.Status.ACTIVE, Reservation.Status.CONFIRMED],
        ).count()
        == 1
    )
    assert (
        ActivityLog.objects.filter(
            action="RESERVATION_OVERRIDDEN",
            target_type="Reservation",
            target_id=str(first.pk),
        ).count()
        == success_logs_before
    )


def test_database_rejects_duplicate_live_reservation_for_item_customer():
    reservation = _reservation()
    duplicate_comment = LiveCommentFactory(
        fb_commenter_id=reservation.customer.facebook_id,
        outcome=LiveComment.Outcome.IGNORED,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Reservation.objects.create(
            item=reservation.item,
            customer=reservation.customer,
            source_comment=duplicate_comment,
            status=Reservation.Status.CONFIRMED,
            payment_deadline=timezone.now() + timedelta(hours=1),
        )


def test_manual_confirm_of_existing_holder_is_a_conflict_not_integrity_error():
    reservation = _reservation()
    comment = LiveCommentFactory(
        fb_commenter_id=reservation.customer.facebook_id,
        outcome=LiveComment.Outcome.IGNORED,
        parsed_code=None,
    )

    response = _client(User.Role.LIVESELLER_ADMIN).post(
        f"/api/mining/mines/{comment.pk}/confirm/",
        {"mining_code": reservation.item.mining_code},
        format="json",
    )

    assert response.status_code == 409
    reservation.refresh_from_db()
    reservation.item.refresh_from_db()
    assert reservation.status == Reservation.Status.ACTIVE
    assert reservation.item.reserved_qty == reservation.qty
    assert (
        Reservation.objects.filter(
            item=reservation.item,
            customer=reservation.customer,
            status__in=[Reservation.Status.ACTIVE, Reservation.Status.CONFIRMED],
        ).count()
        == 1
    )


def test_override_requires_reason_and_valid_new_customer():
    reservation = _reservation()
    client = _client(User.Role.SUPER_ADMIN)

    blank_reason = client.post(
        f"/api/reservations/{reservation.pk}/override/",
        {"new_customer_id": CustomerFactory().pk, "reason": "  "},
        format="json",
    )
    unknown_customer = client.post(
        f"/api/reservations/{reservation.pk}/override/",
        {"new_customer_id": 999999, "reason": "approved"},
        format="json",
    )
    same_customer = client.post(
        f"/api/reservations/{reservation.pk}/override/",
        {"new_customer_id": reservation.customer_id, "reason": "approved"},
        format="json",
    )

    assert blank_reason.status_code == 400
    assert unknown_customer.status_code == 404
    assert same_customer.status_code == 400


def test_override_failure_rolls_back_order_line_and_reservation():
    reservation = _reservation()
    item = reservation.item
    old_order = Order.objects.get(customer=reservation.customer)
    original_total = old_order.total_amount
    new_customer = CustomerFactory()
    from mining import services

    with patch.object(
        services,
        "create_reservation_for_comment",
        side_effect=RuntimeError("forced failure"),
    ):
        with pytest.raises(RuntimeError, match="forced failure"):
            services.override_reservation(
                reservation.pk,
                new_customer_id=new_customer.pk,
                reason="rollback test",
                actor=UserFactory(role=User.Role.SUPER_ADMIN),
            )

    reservation.refresh_from_db()
    item.refresh_from_db()
    old_order.refresh_from_db()
    assert reservation.status == Reservation.Status.ACTIVE
    assert item.reserved_qty == 1
    assert old_order.total_amount == original_total
    assert OrderItem.objects.filter(order=old_order, item=item).exists()
    assert not Order.objects.filter(customer=new_customer).exists()


def test_override_rejects_recipient_price_conflict_without_mutation():
    reservation = _reservation()
    item = reservation.item
    recipient = CustomerFactory()
    recipient_order = Order.objects.create(
        customer=recipient,
        status=Order.Status.UNPAID,
        total_amount=Decimal("10.00"),
    )
    OrderItem.objects.create(
        order=recipient_order,
        item=item,
        quantity=1,
        unit_price=Decimal("10.00"),
    )

    response = _client(User.Role.SUPER_ADMIN).post(
        f"/api/reservations/{reservation.pk}/override/",
        {"new_customer_id": recipient.pk, "reason": "price check"},
        format="json",
    )

    reservation.refresh_from_db()
    assert response.status_code == 409
    assert reservation.status == Reservation.Status.ACTIVE
    recipient_line = OrderItem.objects.get(order=recipient_order, item=item)
    assert recipient_line.quantity == 1


@pytest.mark.parametrize(
    ("path_kind", "role", "allowed"),
    [
        (path_kind, role, allowed)
        for path_kind, action in [
            ("list", "reservations.view"),
            ("manage", "reservations.manage"),
            ("confirm", "mining.confirm"),
            ("override", "reservations.override"),
        ]
        for role, allowed_actions in [
            (
                User.Role.SUPER_ADMIN,
                {
                    "reservations.view",
                    "reservations.manage",
                    "mining.confirm",
                    "reservations.override",
                },
            ),
            (
                User.Role.LIVESELLER_ADMIN,
                {
                    "reservations.view",
                    "reservations.manage",
                    "mining.confirm",
                },
            ),
            (User.Role.ORDERS_ADMIN, {"reservations.view"}),
            (User.Role.PACKING_ADMIN, set()),
            (User.Role.FINANCE_ADMIN, set()),
        ]
        for allowed in [action in allowed_actions]
    ],
)
def test_endpoint_permission_matrix(path_kind, role, allowed):
    reservation = _reservation()
    comment = LiveCommentFactory(
        outcome=LiveComment.Outcome.IGNORED,
        fb_commenter_id="manual",
    )
    client = _client(role)
    with patch("mining.services.enqueue_invoice_after_commit"):
        if path_kind == "list":
            response = client.get("/api/reservations/")
        elif path_kind == "manage":
            response = client.patch(
                f"/api/reservations/{reservation.pk}/",
                {"status": Reservation.Status.CONFIRMED},
                format="json",
            )
        elif path_kind == "confirm":
            response = client.post(
                f"/api/mining/mines/{comment.pk}/confirm/",
                {"mining_code": "101"},
                format="json",
            )
        else:
            response = client.post(
                f"/api/reservations/{reservation.pk}/override/",
                {"new_customer_id": CustomerFactory().pk, "reason": "test"},
                format="json",
            )
    assert response.status_code == (200 if allowed else 403)


@pytest.mark.parametrize(
    "path",
    [
        "/api/reservations/",
        "/api/reservations/1/",
        "/api/reservations/1/override/",
        "/api/mining/mines/1/confirm/",
    ],
)
def test_new_endpoints_reject_anonymous_users(path):
    client = APIClient()
    if path == "/api/reservations/":
        response = client.get(path)
    else:
        response = client.post(path, {}, format="json")
    assert response.status_code in {401, 403}
