"""Services for recording audit events."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from activitylog.models import ActivityLog

if TYPE_CHECKING:
    from accounts.models import User


class ActivityAction:
    """Canonical action names shared by domain services."""

    ITEM_CREATED = "ITEM_CREATED"
    ITEM_UPDATED = "ITEM_UPDATED"
    ITEM_ARCHIVED = "ITEM_ARCHIVED"
    STOCK_ADJUSTED = "STOCK_ADJUSTED"
    STOCK_RESERVED = "STOCK_RESERVED"
    STOCK_RELEASED = "STOCK_RELEASED"
    ITEM_SOLD = "ITEM_SOLD"
    CUSTOMER_CREATED = "CUSTOMER_CREATED"
    CUSTOMER_UPDATED = "CUSTOMER_UPDATED"
    MINE_RESERVED = "MINE_RESERVED"
    MINE_BACKUP = "MINE_BACKUP"
    MINE_IGNORED = "MINE_IGNORED"
    MINE_INVALID = "MINE_INVALID"
    MINE_FLAGGED_LATE = "MINE_FLAGGED_LATE"
    BACKUP_PRIORITY_UPDATED = "BACKUP_PRIORITY_UPDATED"
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_ITEM_ADDED = "ORDER_ITEM_ADDED"
    ORDER_ITEM_UPDATED = "ORDER_ITEM_UPDATED"
    ORDER_TOTAL_UPDATED = "ORDER_TOTAL_UPDATED"
    RESERVATION_CREATED = "RESERVATION_CREATED"
    BACKUP_ADDED = "BACKUP_ADDED"
    INVOICE_SKIPPED = "INVOICE_SKIPPED"
    INVOICE_CREATED = "INVOICE_CREATED"
    INVOICE_UPDATED = "INVOICE_UPDATED"
    INVOICE_LINK_CREATED = "INVOICE_LINK_CREATED"
    INVOICE_SENT = "INVOICE_SENT"
    INVOICE_RECONCILIATION_ENQUEUED = "INVOICE_RECONCILIATION_ENQUEUED"
    RESERVATION_CONFIRMED = "RESERVATION_CONFIRMED"
    RESERVATION_CANCELLED = "RESERVATION_CANCELLED"
    RESERVATION_EXPIRED = "RESERVATION_EXPIRED"
    RESERVATION_OVERRIDDEN = "RESERVATION_OVERRIDDEN"
    RESERVATION_BACKUP_PROMOTED = "RESERVATION_BACKUP_PROMOTED"
    RESERVATION_BACKUP_DROPPED = "RESERVATION_BACKUP_DROPPED"
    RESERVATION_UPDATED = "RESERVATION_UPDATED"


type JsonValue = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


def _json_safe(value: object) -> JsonValue:
    """Convert common domain values to JSON primitives without float conversion."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Activity log JSON object keys must be strings.")
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    raise TypeError(f"Unsupported activity log value: {type(value).__name__}.")


def log_activity(
    *,
    user: User | None,
    action: str,
    target_type: str,
    target_id: str | int | UUID,
    previous_value: object = None,
    new_value: object = None,
    reason: str = "",
) -> ActivityLog:
    """Write an audit record in the caller's current database transaction."""
    return ActivityLog.objects.create(
        user=user,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        previous_value=_json_safe(previous_value),
        new_value=_json_safe(new_value),
        reason=reason,
    )
