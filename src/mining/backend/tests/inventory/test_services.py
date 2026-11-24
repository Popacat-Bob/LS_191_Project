from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from django.db import IntegrityError, close_old_connections, connections, transaction

from inventory.models import InventoryItem
from inventory.services import (
    InsufficientReservedStock,
    InsufficientStock,
    InvalidQuantity,
    find_item,
    manage_inventory,
    mark_sold,
    release,
    reserve,
)
from tests.factories import InventoryItemFactory

pytestmark = pytest.mark.django_db


def test_reserve_release_status_transitions_and_activity_log():
    item = InventoryItemFactory(stock_qty=1, reserved_qty=0)

    reserved = reserve(item, 1)

    assert reserved.reserved_qty == 1
    assert reserved.availability_status == InventoryItem.AvailabilityStatus.RESERVED

    released = release(reserved, 1)

    assert released.reserved_qty == 0
    assert released.availability_status == InventoryItem.AvailabilityStatus.AVAILABLE


def test_reserve_more_than_available_raises_without_negative_counts():
    item = InventoryItemFactory(stock_qty=1, reserved_qty=1)
    item.availability_status = InventoryItem.AvailabilityStatus.RESERVED
    item.save()

    with pytest.raises(InsufficientStock):
        reserve(item, 1)

    item.refresh_from_db()
    assert item.stock_qty == 1
    assert item.reserved_qty == 1


def test_release_more_than_reserved_raises():
    item = InventoryItemFactory(stock_qty=2, reserved_qty=1)

    with pytest.raises(InsufficientReservedStock):
        release(item, 2)

    item.refresh_from_db()
    assert item.reserved_qty == 1


@pytest.mark.parametrize("qty", [0, -1, 1.5, True, "1", None])
def test_reserve_and_release_reject_invalid_quantities(qty):
    item = InventoryItemFactory()

    with pytest.raises(InvalidQuantity):
        reserve(item, qty)
    with pytest.raises(InvalidQuantity):
        release(item, qty)


def test_find_item_normalizes_case_and_whitespace():
    item = InventoryItemFactory(mining_code="MiNe-101")

    assert find_item("  mine-101  ") == item
    assert find_item("missing") is None
    assert find_item("  ") is None


def test_find_item_ignores_sold_and_archived_items():
    sold = InventoryItemFactory(
        mining_code="SOLD-1",
        stock_qty=1,
        reserved_qty=1,
        availability_status=InventoryItem.AvailabilityStatus.RESERVED,
    )
    mark_sold(sold, 1)
    InventoryItemFactory(mining_code="ARCHIVED-1", is_archived=True)

    assert find_item("sold-1") is None
    assert find_item("archived-1") is None


def test_active_mining_code_is_unique_but_reusable_after_sold_or_archived():
    sold = InventoryItemFactory(
        mining_code="REUSE-1",
        stock_qty=1,
        reserved_qty=1,
        availability_status=InventoryItem.AvailabilityStatus.RESERVED,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            InventoryItemFactory(mining_code="REUSE-1")

    mark_sold(sold, 1)
    replacement = InventoryItemFactory(mining_code="REUSE-1")
    assert replacement.pk != sold.pk

    archive_target = InventoryItemFactory(mining_code="REUSE-2")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            InventoryItemFactory(mining_code="REUSE-2")
    manage_inventory(action="archive", item=archive_target)
    assert InventoryItemFactory(mining_code="REUSE-2").pk != archive_target.pk


def test_manage_inventory_create_update_adjust_and_archive():
    item = manage_inventory(
        action="create",
        mining_code="MGMT-1",
        name="Vintage coat",
        selling_price=Decimal("125.50"),
        acquisition_cost="40.00",
        stock_qty=2,
    )
    assert item.stock_qty == 2

    updated = manage_inventory(
        action="update",
        item=item,
        name="Wool coat",
        selling_price=Decimal("135.00"),
        reason="Corrected listing",
    )
    assert updated.name == "Wool coat"
    assert updated.selling_price == Decimal("135.00")

    adjusted = manage_inventory(action="adjust_stock", item=updated, quantity_delta=2)
    assert adjusted.stock_qty == 4

    archived = manage_inventory(action="archive", item=adjusted)
    assert archived.is_archived
    assert find_item("MGMT-1") is None


@pytest.mark.django_db(transaction=True)
def test_concurrent_reservations_cannot_claim_the_last_unit():
    item = InventoryItemFactory(stock_qty=1, reserved_qty=0)
    barrier = Barrier(10)

    def attempt_reservation(item_id: int) -> bool:
        close_old_connections()
        try:
            local_item = InventoryItem.objects.get(pk=item_id)
            barrier.wait(timeout=10)
            try:
                reserve(local_item, 1)
                return True
            except InsufficientStock:
                return False
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(attempt_reservation, [item.pk] * 10))

    item.refresh_from_db()
    assert sum(results) == 1
    assert item.stock_qty == 1
    assert item.reserved_qty == 1
    assert item.reserved_qty <= item.stock_qty
