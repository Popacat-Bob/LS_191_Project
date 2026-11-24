"""Inventory API routes."""

from django.urls import path

from inventory.views import (
    InventoryItemDetailView,
    InventoryItemListView,
    StockAdjustmentView,
)

urlpatterns = [
    path("items/", InventoryItemListView.as_view(), name="inventory-item-list"),
    path(
        "items/<int:item_id>/",
        InventoryItemDetailView.as_view(),
        name="inventory-item-detail",
    ),
    path(
        "items/<int:item_id>/adjust-stock/",
        StockAdjustmentView.as_view(),
        name="inventory-stock-adjust",
    ),
]
