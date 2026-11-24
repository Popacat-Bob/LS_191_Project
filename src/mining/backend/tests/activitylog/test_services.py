from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from activitylog.models import ActivityLog
from activitylog.services import log_activity


@pytest.mark.django_db
def test_log_activity_serializes_decimal_datetime_and_uuid():
    identifier = UUID("8af17a56-b121-4f82-b502-4bc7c3d57e39")
    timestamp = datetime(2026, 11, 10, 12, 30, tzinfo=UTC)

    entry = log_activity(
        user=None,
        action="TEST",
        target_type="InventoryItem",
        target_id=identifier,
        previous_value={"price": Decimal("12.34"), "updated_at": timestamp},
        new_value={"price": Decimal("56.78"), "id": identifier},
    )

    entry.refresh_from_db()
    assert entry.user is None
    assert entry.target_id == str(identifier)
    assert entry.previous_value == {
        "price": "12.34",
        "updated_at": timestamp.isoformat(),
    }
    assert entry.new_value == {"price": "56.78", "id": str(identifier)}
    assert ActivityLog.objects.filter(pk=entry.pk).exists()


@pytest.mark.django_db
def test_log_activity_rejects_non_string_json_object_keys():
    with pytest.raises(TypeError, match="keys must be strings"):
        log_activity(
            user=None,
            action="TEST",
            target_type="InventoryItem",
            target_id=1,
            new_value={1: "not a JSON object key"},
        )
