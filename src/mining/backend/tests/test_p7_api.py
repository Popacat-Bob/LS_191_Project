import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

import pytest
from django.contrib import admin
from django.db.models import Count, Sum
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from accounts.permissions import (
    ACTIVITYLOG_VIEW,
    INVENTORY_MANAGE,
    INVENTORY_VIEW,
    MINING_CONFIRM,
    MINING_MONITOR,
    ORDERS_SEND_INVOICE,
    ORDERS_VIEW,
    RESERVATIONS_MANAGE,
    RESERVATIONS_OVERRIDE,
    RESERVATIONS_VIEW,
    can,
)
from activitylog.models import ActivityLog
from integrations.models import WebhookEvent
from inventory.models import InventoryItem
from inventory.services import manage_inventory
from invoicing.models import Invoice
from mining.expiry import expire_due_reservations
from mining.models import LiveComment, Reservation
from mining.views import simulate_comment_view
from orders.models import Order
from tests.factories import (
    ActivityLogFactory,
    InventoryItemFactory,
    InvoiceFactory,
    LiveCommentFactory,
    OrderFactory,
    OrderItemFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db

ROLE_VALUES = [
    User.Role.SUPER_ADMIN,
    User.Role.LIVESELLER_ADMIN,
    User.Role.ORDERS_ADMIN,
    User.Role.PACKING_ADMIN,
    User.Role.FINANCE_ADMIN,
    None,
]

ROUTE_MATRIX = [
    ("GET", "/api/mining/comments/", MINING_MONITOR, None),
    ("POST", "/api/mining/comments/simulate/", MINING_CONFIRM, {}),
    (
        "POST",
        "/api/mining/mines/999999/confirm/",
        MINING_CONFIRM,
        {"mining_code": "101"},
    ),
    ("GET", "/api/reservations/", RESERVATIONS_VIEW, None),
    (
        "PATCH",
        "/api/reservations/999999/",
        RESERVATIONS_MANAGE,
        {"status": Reservation.Status.CONFIRMED},
    ),
    (
        "POST",
        "/api/reservations/999999/override/",
        RESERVATIONS_OVERRIDE,
        {"new_customer_id": 999999, "reason": "matrix test"},
    ),
    ("GET", "/api/inventory/items/", INVENTORY_VIEW, None),
    (
        "POST",
        "/api/inventory/items/",
        INVENTORY_MANAGE,
        {
            "mining_code": "MATRIX",
            "name": "Matrix Item",
            "selling_price": "1.00",
            "acquisition_cost": "0.50",
        },
    ),
    ("GET", "/api/inventory/items/999999/", INVENTORY_VIEW, None),
    ("PATCH", "/api/inventory/items/999999/", INVENTORY_MANAGE, {"name": "New"}),
    ("PUT", "/api/inventory/items/999999/", INVENTORY_MANAGE, {"name": "New"}),
    ("DELETE", "/api/inventory/items/999999/", INVENTORY_MANAGE, None),
    (
        "POST",
        "/api/inventory/items/999999/adjust-stock/",
        INVENTORY_MANAGE,
        {"quantity_delta": 1},
    ),
    ("GET", "/api/orders/", ORDERS_VIEW, None),
    ("POST", "/api/orders/999999/send-invoice/", ORDERS_SEND_INVOICE, {}),
    ("GET", "/api/activity-log/", ACTIVITYLOG_VIEW, None),
    ("GET", "/api/webhooks/facebook/", None, None),
    ("POST", "/api/webhooks/facebook/", None, {}),
]


def _client_for(role: str | None) -> APIClient:
    client = APIClient()
    if role is not None:
        client.force_authenticate(user=UserFactory(role=role))
    return client


def _request(client: APIClient, method: str, path: str, payload):
    request_method = getattr(client, method.lower())
    if method == "GET":
        return request_method(path)
    if method == "DELETE":
        return request_method(path)
    return request_method(path, payload, format="json")


@pytest.mark.parametrize(("method", "path", "action", "payload"), ROUTE_MATRIX)
@pytest.mark.parametrize("role", ROLE_VALUES)
@override_settings(DEBUG=True)
def test_api_route_permission_matrix(method, path, action, payload, role):
    client = _client_for(role)

    response = _request(client, method, path, payload)

    if action is None:
        assert response.status_code == 403
    elif role is None or not can(client.handler._force_user, action):
        assert response.status_code == 403
    else:
        assert response.status_code not in {401, 403}


def test_project_pagination_default_and_cap():
    for index in range(110):
        InventoryItemFactory(mining_code=f"PAGE-{index:03d}")
    client = _client_for(User.Role.SUPER_ADMIN)

    default_response = client.get("/api/inventory/items/")
    capped_response = client.get("/api/inventory/items/?page_size=1000")

    assert default_response.status_code == capped_response.status_code == 200
    assert len(default_response.data["results"]) == 25
    assert len(capped_response.data["results"]) == 100
    assert capped_response.data["count"] == 110


def test_simulate_endpoint_declares_mining_confirm_permission():
    permission_classes = simulate_comment_view.cls.permission_classes

    assert len(permission_classes) == 1
    assert permission_classes[0].required_action == MINING_CONFIRM


def test_admin_login_creates_session_accepted_by_api():
    user = UserFactory(role=User.Role.ORDERS_ADMIN)
    user.is_staff = True
    user.set_password("development-password")
    user.save(update_fields=["is_staff", "password"])
    client = APIClient()

    login_response = client.post(
        "/admin/login/",
        {
            "username": user.email,
            "password": "development-password",
            "next": "/",
        },
    )
    api_response = client.get("/api/orders/")

    assert login_response.status_code == 302
    assert api_response.status_code == 200


def test_mining_comments_filters_and_exposes_raw_and_normalized_message():
    now = timezone.now()
    first = LiveCommentFactory(
        live_video_id="stream-a",
        created_time=now,
        message="mine 101",
        raw_message="  MINE   101 ",
        outcome=LiveComment.Outcome.RESERVED,
    )
    LiveCommentFactory(
        live_video_id="stream-b",
        created_time=now - timedelta(minutes=5),
        outcome=LiveComment.Outcome.BACKUP,
    )
    client = _client_for(User.Role.LIVESELLER_ADMIN)

    response = client.get(
        "/api/mining/comments/?"
        + urlencode(
            {
                "live_video_id": "stream-a",
                "since": now.isoformat(),
                "outcome": "RESERVED",
            }
        )
    )
    bad_since = client.get("/api/mining/comments/?since=not-a-time")

    assert response.status_code == 200
    assert response.data["count"] == 1
    row = response.data["results"][0]
    assert row["id"] == first.pk
    assert row["message"] == "mine 101"
    assert row["raw_message"] == "  MINE   101 "
    assert bad_since.status_code == 400
    assert "error" in bad_since.data


def test_inventory_crud_search_filters_archive_and_stock_workflow():
    client = _client_for(User.Role.LIVESELLER_ADMIN)
    create = client.post(
        "/api/inventory/items/",
        {
            "mining_code": "COAT-1",
            "name": "Blue Coat",
            "description": "Thrifted",
            "selling_price": "125.50",
            "acquisition_cost": "30.00",
            "stock_qty": 2,
        },
        format="json",
    )
    item_id = create.data["id"]
    search = client.get("/api/inventory/items/?search=blue")
    update = client.patch(
        f"/api/inventory/items/{item_id}/",
        {"name": "Navy Coat"},
        format="json",
    )
    forbidden_stock = client.patch(
        f"/api/inventory/items/{item_id}/",
        {"stock_qty": 99},
        format="json",
    )
    adjustment = client.post(
        f"/api/inventory/items/{item_id}/adjust-stock/",
        {"quantity_delta": 1},
        format="json",
    )
    available_list = client.get("/api/inventory/items/?availability_status=AVAILABLE")
    archived = client.delete(f"/api/inventory/items/{item_id}/")
    archived_list = client.get("/api/inventory/items/?is_archived=true")

    assert create.status_code == 201
    assert search.data["results"][0]["id"] == item_id
    assert update.status_code == 200
    assert update.data["name"] == "Navy Coat"
    assert forbidden_stock.status_code == 400
    assert adjustment.data["stock_qty"] == 3
    assert available_list.data["count"] == 1
    assert archived.status_code == 200
    assert archived.data["is_archived"] is True
    assert archived_list.data["count"] == 1
    assert InventoryItem.objects.filter(pk=item_id).exists()


@pytest.mark.parametrize("method", ["patch", "put"])
@pytest.mark.parametrize("field", ["stock_qty", "reserved_qty", "availability_status"])
def test_inventory_update_rejects_all_managed_stock_fields(method, field):
    item = InventoryItemFactory()
    response = getattr(_client_for(User.Role.SUPER_ADMIN), method)(
        f"/api/inventory/items/{item.pk}/",
        {field: 1},
        format="json",
    )
    assert response.status_code == 400
    assert "error" in response.data


def test_order_list_filters_lines_invoice_summary_and_invoice_send_conflict():
    client = _client_for(User.Role.ORDERS_ADMIN)
    order = OrderFactory(status=Order.Status.UNPAID)
    item = InventoryItemFactory()
    OrderItemFactory(order=order, item=item, quantity=2, unit_price=Decimal("3.25"))
    order.total_amount = Decimal("6.50")
    order.save(update_fields=["total_amount"])
    invoice = InvoiceFactory(order=order, total=Decimal("6.50"))
    response = client.get(f"/api/orders/?status=UNPAID&customer={order.customer_id}")
    assert response.status_code == 200
    serialized = response.data["results"][0]
    assert serialized["total_amount"] == "6.50"
    assert serialized["items"][0]["unit_price"] == "3.25"
    assert serialized["invoice_summary"]["invoice_number"] == invoice.invoice_number

    paid = OrderFactory(status=Order.Status.PAID)
    skipped = client.post(f"/api/orders/{paid.pk}/send-invoice/", {}, format="json")
    empty = OrderFactory(status=Order.Status.UNPAID)
    skipped_empty = client.post(
        f"/api/orders/{empty.pk}/send-invoice/", {}, format="json"
    )
    assert skipped.status_code == skipped_empty.status_code == 409
    assert skipped.data["error"]["code"] == "invoice_skipped"


def test_order_send_invoice_returns_202_and_summary():
    order = OrderFactory(status=Order.Status.UNPAID)
    item = InventoryItemFactory()
    OrderItemFactory(order=order, item=item, quantity=1, unit_price=Decimal("8.50"))
    order.total_amount = Decimal("8.50")
    order.save(update_fields=["total_amount"])

    with override_settings(PAYMENT_PROVIDER="fake", MESSAGE_SENDER="console"):
        response = _client_for(User.Role.ORDERS_ADMIN).post(
            f"/api/orders/{order.pk}/send-invoice/",
            {},
            format="json",
        )

    assert response.status_code == 202
    assert response.data["status"] == Invoice.Status.SENT


def test_activity_log_filters_and_invalid_date_range():
    actor = UserFactory()
    matching = ActivityLogFactory(
        user=actor,
        action="TEST_ACTION",
        target_type="Order",
        target_id="123",
    )
    ActivityLogFactory(action="OTHER", target_type="Item")
    client = _client_for(User.Role.SUPER_ADMIN)

    response = client.get(
        "/api/activity-log/?"
        + urlencode(
            {
                "action": "TEST_ACTION",
                "target_type": "Order",
                "target_id": "123",
                "user": actor.pk,
                "created_from": (
                    matching.created_at - timedelta(seconds=1)
                ).isoformat(),
                "created_to": (matching.created_at + timedelta(seconds=1)).isoformat(),
            }
        )
    )
    bad_range = client.get(
        "/api/activity-log/?created_from=2026-11-11T00:00:00Z"
        "&created_to=2026-11-10T00:00:00Z"
    )
    bad_timestamp = client.get("/api/activity-log/?created_from=invalid")

    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == matching.pk
    assert bad_range.status_code == 400
    assert bad_timestamp.status_code == 400


def test_admin_protects_managed_fields_and_disables_audit_event_mutations():
    request = type("Request", (), {})()
    inventory_admin = admin.site._registry[InventoryItem]
    reservation_admin = admin.site._registry[Reservation]
    order_admin = admin.site._registry[Order]
    activity_admin = admin.site._registry[ActivityLog]
    webhook_admin = admin.site._registry[WebhookEvent]

    assert {"stock_qty", "reserved_qty", "availability_status"} <= set(
        inventory_admin.get_readonly_fields(request)
    )
    assert "status" in reservation_admin.get_readonly_fields(request)
    assert "total_amount" in order_admin.get_readonly_fields(request)
    assert activity_admin.has_add_permission(request) is False
    assert activity_admin.has_change_permission(request) is False
    assert activity_admin.has_delete_permission(request) is False
    assert webhook_admin.has_delete_permission(request) is False


@pytest.mark.django_db(transaction=True)
@override_settings(
    DEBUG=True,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    PAYMENT_PROVIDER="fake",
    MESSAGE_SENDER="console",
    INVOICE_DELAY_SECONDS=0,
    FB_APP_SECRET="milestone-secret",
)
def test_milestone_simulation_webhook_invoice_expiry_promotion(caplog):
    caplog.set_level("INFO", logger="invoicing.senders")
    item = manage_inventory(
        action="create",
        mining_code="MILESTONE-101",
        name="Milestone Jacket",
        selling_price=Decimal("249.95"),
        acquisition_cost=Decimal("50.00"),
        stock_qty=1,
    )
    client = APIClient()
    client.force_authenticate(user=UserFactory(role=User.Role.LIVESELLER_ADMIN))

    simulated = client.post(
        "/api/mining/comments/simulate/",
        {
            "code": item.mining_code,
            "name": "First Buyer",
            "fb_commenter_id": "milestone-first",
            "message": "  MINE   MILESTONE-101 ",
        },
        format="json",
    )
    assert simulated.status_code == 200
    first_comment = LiveComment.objects.get(
        fb_comment_id=simulated.data["fb_comment_id"]
    )
    first_reservation = Reservation.objects.get(pk=simulated.data["reservation_id"])
    first_order = Order.objects.get(customer=first_reservation.customer)
    first_invoice = Invoice.objects.get(order=first_order)

    webhook_payload = {
        "comments": [
            {
                "fb_comment_id": "milestone-webhook-comment",
                "live_video_id": "milestone-live",
                "message": "mine MILESTONE-101",
                "fb_commenter_id": "milestone-second",
                "fb_commenter_name": "Second Buyer",
                "created_time": timezone.now().isoformat(),
            }
        ]
    }
    raw_body = json.dumps(webhook_payload, separators=(",", ":")).encode()
    signature = hmac.new(b"milestone-secret", raw_body, hashlib.sha256).hexdigest()
    webhook = APIClient().generic(
        "POST",
        "/api/webhooks/facebook/",
        data=raw_body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=f"sha256={signature}",
    )
    second_comment = LiveComment.objects.get(fb_comment_id="milestone-webhook-comment")

    assert webhook.status_code == 200
    assert first_comment.outcome == LiveComment.Outcome.RESERVED
    assert first_comment.raw_message == "  MINE   MILESTONE-101 "
    assert first_comment.message == "mine milestone-101"
    assert first_reservation.status == Reservation.Status.ACTIVE
    assert first_order.status == Order.Status.UNPAID
    assert first_order.total_amount == Decimal("249.95")
    assert first_invoice.status == Invoice.Status.SENT
    assert first_invoice.payment_link_url.startswith("https://fake-pay.local/links/")
    assert "-> Messenger to First Buyer" in caplog.text
    assert second_comment.outcome == LiveComment.Outcome.BACKUP

    first_reservation.payment_deadline = timezone.now() - timedelta(minutes=1)
    first_reservation.save(update_fields=["payment_deadline"])
    result = expire_due_reservations()
    first_reservation.refresh_from_db()
    second_comment.refresh_from_db()
    promoted = Reservation.objects.get(customer__facebook_id="milestone-second")
    promoted_order = Order.objects.get(customer=promoted.customer)
    promoted_invoice = Invoice.objects.get(order=promoted_order)

    assert result["expired"] == 1
    assert first_reservation.status == Reservation.Status.EXPIRED
    assert promoted.status == Reservation.Status.ACTIVE
    assert second_comment.reservation_id == promoted.pk
    assert promoted_invoice.status == Invoice.Status.SENT
    assert ActivityLog.objects.filter(target_id="None").count() == 0

    for current_item in InventoryItem.objects.all():
        live_qty = (
            Reservation.objects.filter(
                item=current_item,
                status__in=[Reservation.Status.ACTIVE, Reservation.Status.CONFIRMED],
            ).aggregate(total=Sum("qty"))["total"]
            or 0
        )
        assert current_item.reserved_qty == live_qty
        assert 0 <= current_item.reserved_qty <= current_item.stock_qty
    for unpaid_order in Order.objects.filter(status=Order.Status.UNPAID):
        expected = sum(
            (line.quantity * line.unit_price for line in unpaid_order.items.all()),
            Decimal("0.00"),
        )
        assert unpaid_order.total_amount == expected
    assert not (
        Reservation.objects.filter(
            status__in=[Reservation.Status.ACTIVE, Reservation.Status.CONFIRMED]
        )
        .values("item_id", "customer_id")
        .annotate(count=Count("pk"))
        .filter(count__gt=1)
        .exists()
    )
