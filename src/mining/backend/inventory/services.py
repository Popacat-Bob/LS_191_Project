"""Transactional services for inventory lookup and stock management."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from django.db import transaction

from activitylog.services import ActivityAction, log_activity
from inventory.models import InventoryItem

if TYPE_CHECKING:
    from accounts.models import User


class InventoryError(Exception):
    """Base error for rejected inventory operations."""


class InsufficientStock(InventoryError):
    """Raised when an operation requires more available stock than exists."""


class InsufficientReservedStock(InventoryError):
    """Raised when releasing or selling more than the reserved quantity."""


class InvalidQuantity(InventoryError):
    """Raised when a quantity is not a valid positive integer."""


class InvalidInventoryAction(InventoryError):
    """Raised for unsupported management operations or fields."""


class InvalidInventoryState(InventoryError):
    """Raised when a stock change would violate inventory invariants."""


_EDITABLE_FIELDS = frozenset(
    {
        "mining_code",
        "name",
        "description",
        "selling_price",
        "acquisition_cost",
    }
)
_MONEY_FIELDS = frozenset({"selling_price", "acquisition_cost"})
_MANAGED_FIELDS = _EDITABLE_FIELDS | {
    "stock_qty",
    "reserved_qty",
    "availability_status",
    "is_archived",
    "updated_at",
}


def _positive_quantity(qty: int) -> None:
    """Validate a positive integer quantity, excluding bool values."""
    if type(qty) is not int or qty <= 0:
        raise InvalidQuantity("Quantity must be a positive integer.")


def _available_qty(item: InventoryItem) -> int:
    """Return the number of unreserved units."""
    return item.stock_qty - item.reserved_qty


def _derive_status(item: InventoryItem, *, sold: bool = False) -> str:
    """Set availability status from stock counts; only an explicit sale marks SOLD."""
    if sold:
        return InventoryItem.AvailabilityStatus.SOLD
    if _available_qty(item) > 0:
        return InventoryItem.AvailabilityStatus.AVAILABLE
    if item.reserved_qty > 0:
        return InventoryItem.AvailabilityStatus.RESERVED
    raise InvalidInventoryState(
        "An item cannot have zero stock unless it is explicitly marked sold."
    )


def _snapshot(item: InventoryItem) -> dict[str, object]:
    """Capture the user-facing and stock fields needed for an audit record."""
    return {
        "mining_code": item.mining_code,
        "name": item.name,
        "description": item.description,
        "selling_price": item.selling_price,
        "acquisition_cost": item.acquisition_cost,
        "stock_qty": item.stock_qty,
        "reserved_qty": item.reserved_qty,
        "availability_status": item.availability_status,
        "is_archived": item.is_archived,
    }


def lock_item(item: InventoryItem) -> InventoryItem:
    """Re-fetch and lock an inventory row; call inside ``transaction.atomic()``."""
    return InventoryItem.objects.select_for_update().get(pk=item.pk)


def find_item(code: str) -> InventoryItem | None:
    """Find an active item by a case-insensitive, whitespace-trimmed code."""
    normalized_code = code.strip()
    if not normalized_code:
        return None
    return (
        InventoryItem.objects.filter(
            mining_code__iexact=normalized_code,
            is_archived=False,
        )
        .exclude(availability_status=InventoryItem.AvailabilityStatus.SOLD)
        .first()
    )


def reserve(
    item: InventoryItem, qty: int, *, actor: User | None = None
) -> InventoryItem:
    """Reserve available units under a row lock and audit the stock change."""
    _positive_quantity(qty)
    with transaction.atomic():
        locked = lock_item(item)
        if locked.is_archived or locked.availability_status == "SOLD":
            raise InsufficientStock("Archived or sold items cannot be reserved.")
        if _available_qty(locked) < qty:
            raise InsufficientStock(
                f"Requested {qty} units but only "
                f"{_available_qty(locked)} are available."
            )
        previous = _snapshot(locked)
        locked.reserved_qty += qty
        locked.availability_status = _derive_status(locked)
        locked.save(update_fields=["reserved_qty", "availability_status", "updated_at"])
        log_activity(
            user=actor,
            action=ActivityAction.STOCK_RESERVED,
            target_type="InventoryItem",
            target_id=locked.pk,
            previous_value=previous,
            new_value=_snapshot(locked),
        )
        return locked


def release(
    item: InventoryItem, qty: int, *, actor: User | None = None
) -> InventoryItem:
    """Release reserved units under a row lock and audit the stock change."""
    _positive_quantity(qty)
    with transaction.atomic():
        locked = lock_item(item)
        if locked.reserved_qty < qty:
            raise InsufficientReservedStock(
                f"Cannot release {qty} units; only {locked.reserved_qty} are reserved."
            )
        previous = _snapshot(locked)
        locked.reserved_qty -= qty
        locked.availability_status = _derive_status(locked)
        locked.save(update_fields=["reserved_qty", "availability_status", "updated_at"])
        log_activity(
            user=actor,
            action=ActivityAction.STOCK_RELEASED,
            target_type="InventoryItem",
            target_id=locked.pk,
            previous_value=previous,
            new_value=_snapshot(locked),
        )
        return locked


def mark_sold(
    item: InventoryItem, qty: int, *, actor: User | None = None
) -> InventoryItem:
    """Mark reserved units as sold, reducing total and reserved stock together."""
    _positive_quantity(qty)
    with transaction.atomic():
        locked = lock_item(item)
        if locked.reserved_qty < qty:
            raise InsufficientReservedStock(
                f"Cannot sell {qty} units; only {locked.reserved_qty} are reserved."
            )
        previous = _snapshot(locked)
        locked.stock_qty -= qty
        locked.reserved_qty -= qty
        locked.availability_status = _derive_status(locked, sold=locked.stock_qty == 0)
        locked.save(
            update_fields=[
                "stock_qty",
                "reserved_qty",
                "availability_status",
                "updated_at",
            ]
        )
        log_activity(
            user=actor,
            action=ActivityAction.ITEM_SOLD,
            target_type="InventoryItem",
            target_id=locked.pk,
            previous_value=previous,
            new_value=_snapshot(locked),
        )
        return locked


def _validate_fields(values: dict[str, Any], allowed: frozenset[str]) -> None:
    """Reject unknown fields and convert monetary inputs to Decimal."""
    unknown = values.keys() - allowed
    if unknown:
        raise InvalidInventoryAction(
            f"Unsupported inventory fields: {', '.join(sorted(unknown))}."
        )
    for field in _MONEY_FIELDS & values.keys():
        value = values[field]
        if isinstance(value, float) or not isinstance(value, (str, int, Decimal)):
            raise InvalidInventoryState(f"{field} must be a Decimal-compatible value.")
        try:
            values[field] = Decimal(value)
        except InvalidOperation as exc:
            message = f"{field} must be a valid decimal amount."
            raise InvalidInventoryState(message) from exc
        if not values[field].is_finite() or values[field] < 0:
            message = f"{field} must be a finite non-negative amount."
            raise InvalidInventoryState(message)


def manage_inventory(
    *,
    action: str,
    item: InventoryItem | None = None,
    actor: User | None = None,
    reason: str = "",
    **values: Any,
) -> InventoryItem:
    """Create, update, adjust, or archive an item and write an audit event."""
    with transaction.atomic():
        if action == "create":
            if item is not None:
                raise InvalidInventoryAction("Create does not accept an existing item.")
            _validate_fields(values, _EDITABLE_FIELDS | {"stock_qty"})
            stock_qty = values.pop("stock_qty", 1)
            if type(stock_qty) is not int or stock_qty <= 0:
                raise InvalidQuantity("Initial stock must be a positive integer.")
            created = InventoryItem(**values, stock_qty=stock_qty)
            created.availability_status = _derive_status(created)
            created.save()
            log_activity(
                user=actor,
                action=ActivityAction.ITEM_CREATED,
                target_type="InventoryItem",
                target_id=created.pk,
                new_value=_snapshot(created),
                reason=reason,
            )
            return created

        if item is None:
            raise InvalidInventoryAction(f"{action} requires an item.")
        locked = lock_item(item)
        previous = _snapshot(locked)

        if action == "update":
            _validate_fields(values, _EDITABLE_FIELDS)
            for field, value in values.items():
                setattr(locked, field, value)
            if values:
                locked.save(update_fields=[*values, "updated_at"])
            audit_action = ActivityAction.ITEM_UPDATED
        elif action == "adjust_stock":
            _validate_fields(values, frozenset({"quantity_delta"}))
            delta = values.get("quantity_delta")
            if type(delta) is not int or delta == 0:
                raise InvalidQuantity("Stock adjustment must be a non-zero integer.")
            new_stock = locked.stock_qty + delta
            if new_stock <= 0:
                raise InvalidInventoryState(
                    "Stock cannot be reduced to zero; use mark_sold for sold units."
                )
            if new_stock < locked.reserved_qty:
                raise InsufficientStock(
                    "Stock cannot be reduced below the quantity already reserved."
                )
            locked.stock_qty = new_stock
            locked.availability_status = _derive_status(locked)
            locked.save(
                update_fields=["stock_qty", "availability_status", "updated_at"]
            )
            audit_action = ActivityAction.STOCK_ADJUSTED
        elif action == "archive":
            if values:
                raise InvalidInventoryAction("Archive does not accept extra fields.")
            locked.is_archived = True
            locked.save(update_fields=["is_archived", "updated_at"])
            audit_action = ActivityAction.ITEM_ARCHIVED
        else:
            raise InvalidInventoryAction(f"Unknown inventory action: {action!r}.")

        log_activity(
            user=actor,
            action=audit_action,
            target_type="InventoryItem",
            target_id=locked.pk,
            previous_value=previous,
            new_value=_snapshot(locked),
            reason=reason,
        )
        return locked
