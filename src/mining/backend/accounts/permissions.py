"""Server-side action authorization."""

from rest_framework.permissions import BasePermission

MINING_MONITOR = "mining.monitor"
MINING_CONFIRM = "mining.confirm"
RESERVATIONS_VIEW = "reservations.view"
RESERVATIONS_MANAGE = "reservations.manage"
RESERVATIONS_OVERRIDE = "reservations.override"
INVENTORY_VIEW = "inventory.view"
INVENTORY_MANAGE = "inventory.manage"
ORDERS_VIEW = "orders.view"
ORDERS_SEND_INVOICE = "orders.send_invoice"
INVOICES_VIEW = "invoices.view"
PAYMENTS_VIEW = "payments.view"
ACTIVITYLOG_VIEW = "activitylog.view"
USERS_MANAGE = "users.manage"

ALL_ACTIONS = frozenset(
    {
        MINING_MONITOR,
        MINING_CONFIRM,
        RESERVATIONS_VIEW,
        RESERVATIONS_MANAGE,
        RESERVATIONS_OVERRIDE,
        INVENTORY_VIEW,
        INVENTORY_MANAGE,
        ORDERS_VIEW,
        ORDERS_SEND_INVOICE,
        INVOICES_VIEW,
        PAYMENTS_VIEW,
        ACTIVITYLOG_VIEW,
        USERS_MANAGE,
    }
)

_ROLE_ACTIONS: dict[str, frozenset[str]] = {
    "LIVESELLER_ADMIN": frozenset(
        {
            MINING_MONITOR,
            MINING_CONFIRM,
            RESERVATIONS_VIEW,
            RESERVATIONS_MANAGE,
            INVENTORY_VIEW,
            INVENTORY_MANAGE,
        }
    ),
    "ORDERS_ADMIN": frozenset(
        {
            ORDERS_VIEW,
            ORDERS_SEND_INVOICE,
            RESERVATIONS_VIEW,
            INVENTORY_VIEW,
        }
    ),
    "PACKING_ADMIN": frozenset({ORDERS_VIEW, INVENTORY_VIEW}),
    "FINANCE_ADMIN": frozenset(
        {ORDERS_VIEW, INVOICES_VIEW, PAYMENTS_VIEW, INVENTORY_VIEW}
    ),
}


def can(user: object, action: str) -> bool:
    """Return whether an authenticated user may perform a known action."""
    if action not in ALL_ACTIONS or user is None:
        return False
    if not getattr(user, "is_authenticated", False):
        return False

    role = getattr(user, "role", None)
    if role == "SUPER_ADMIN":
        return True
    return action in _ROLE_ACTIONS.get(role, frozenset())


def HasAction(action: str) -> type[BasePermission]:
    """Build a DRF permission class that delegates authorization to ``can``."""

    class _HasAction(BasePermission):
        """Permission class scoped to one action."""

        required_action = action

        def has_permission(self, request: object, view: object) -> bool:
            """Check the request user against the configured action."""
            return can(getattr(request, "user", None), self.required_action)

    _HasAction.__name__ = f"HasAction_{action.replace('.', '_')}"
    return _HasAction
