from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import InventoryItem
from invoicing.tasks import send_invoice
from tests.factories import InventoryItemFactory, UserFactory


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_simulate_endpoint_reserves_then_adds_second_commenter_to_backup():
    InventoryItemFactory(mining_code="101", stock_qty=1)
    user = UserFactory(role=User.Role.LIVESELLER_ADMIN)
    client = APIClient()
    client.force_authenticate(user=user)

    with patch.object(send_invoice, "delay"):
        reserved = client.post(
            "/api/mining/comments/simulate/",
            {"code": "101", "name": "First Buyer", "fb_commenter_id": "first"},
            format="json",
        )
        backup = client.post(
            "/api/mining/comments/simulate/",
            {"code": "101", "name": "Second Buyer", "fb_commenter_id": "second"},
            format="json",
        )
    assert reserved.status_code == 200
    assert reserved.data["outcome"] == "RESERVED"
    assert backup.status_code == 200
    assert backup.data["outcome"] == "BACKUP"
    assert InventoryItem.objects.get(mining_code="101").reserved_qty == 1


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_simulate_endpoint_is_not_found_when_debug_is_off():
    client = APIClient()

    response = client.post(
        "/api/mining/comments/simulate/",
        {"code": "101", "name": "Buyer"},
        format="json",
    )

    assert response.status_code == 404


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_simulate_endpoint_forbids_unauthorized_role():
    client = APIClient()
    client.force_authenticate(user=UserFactory(role=User.Role.ORDERS_ADMIN))

    response = client.post(
        "/api/mining/comments/simulate/",
        {"code": "101", "name": "Buyer"},
        format="json",
    )

    assert response.status_code == 403
