from types import SimpleNamespace

import pytest
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts import permissions
from accounts.models import User

ROLE_ACTIONS = {
    User.Role.SUPER_ADMIN: permissions.ALL_ACTIONS,
    User.Role.LIVESELLER_ADMIN: {
        permissions.MINING_MONITOR,
        permissions.MINING_CONFIRM,
        permissions.RESERVATIONS_VIEW,
        permissions.RESERVATIONS_MANAGE,
        permissions.INVENTORY_VIEW,
        permissions.INVENTORY_MANAGE,
    },
    User.Role.ORDERS_ADMIN: {
        permissions.ORDERS_VIEW,
        permissions.ORDERS_SEND_INVOICE,
        permissions.RESERVATIONS_VIEW,
        permissions.INVENTORY_VIEW,
    },
    User.Role.PACKING_ADMIN: {permissions.ORDERS_VIEW, permissions.INVENTORY_VIEW},
    User.Role.FINANCE_ADMIN: {
        permissions.ORDERS_VIEW,
        permissions.INVOICES_VIEW,
        permissions.PAYMENTS_VIEW,
        permissions.INVENTORY_VIEW,
    },
}


@pytest.mark.parametrize(
    ("role", "action", "expected"),
    [
        (role, action, action in allowed)
        for role, allowed in ROLE_ACTIONS.items()
        for action in sorted(permissions.ALL_ACTIONS)
    ],
)
def test_can_matches_every_role_action_pair(role, action, expected):
    user = SimpleNamespace(is_authenticated=True, role=role)

    assert permissions.can(user, action) is expected


@pytest.mark.parametrize("user", [None, SimpleNamespace(is_authenticated=False)])
def test_can_denies_missing_or_unauthenticated_user(user):
    assert permissions.can(user, permissions.INVENTORY_VIEW) is False


def test_can_denies_unknown_action_even_for_super_admin():
    user = SimpleNamespace(is_authenticated=True, role=User.Role.SUPER_ADMIN)

    assert permissions.can(user, "unknown.action") is False


def test_has_action_returns_403_for_authenticated_user_without_action(
    db, django_user_model
):
    action = permissions.HasAction(permissions.INVENTORY_MANAGE)

    @api_view(["GET"])
    @permission_classes([action])
    def protected_view(request: Request) -> Response:
        return Response({"ok": True})

    user = django_user_model.objects.create_user(
        email="orders@example.com",
        password="test-password",
        name="Orders Admin",
        role=User.Role.ORDERS_ADMIN,
    )
    request = APIRequestFactory().get("/")
    force_authenticate(request, user=user)

    assert protected_view(request).status_code == 403


def test_has_action_allows_authenticated_user_with_action(django_user_model):
    action = permissions.HasAction(permissions.INVENTORY_VIEW)

    @api_view(["GET"])
    @permission_classes([action])
    def protected_view(request: Request) -> Response:
        return Response({"ok": True})

    user = django_user_model.objects.create_user(
        email="packing@example.com",
        password="test-password",
        name="Packing Admin",
        role=User.Role.PACKING_ADMIN,
    )
    request = APIRequestFactory().get("/")
    force_authenticate(request, user=user)

    assert protected_view(request).status_code == 200
