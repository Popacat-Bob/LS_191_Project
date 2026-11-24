"""Inventory API views; lifecycle rules remain in inventory.services."""

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.permissions import INVENTORY_MANAGE, INVENTORY_VIEW, HasAction
from inventory.models import InventoryItem
from inventory.serializers import (
    InventoryFilterSerializer,
    InventoryItemCreateSerializer,
    InventoryItemSerializer,
    InventoryItemUpdateSerializer,
    StockAdjustmentSerializer,
)
from inventory.services import manage_inventory


class InventoryItemListView(generics.ListCreateAPIView):
    """Searchable inventory listing and audited item creation."""

    serializer_class = InventoryItemSerializer

    def get_permissions(self):
        action = INVENTORY_MANAGE if self.request.method == "POST" else INVENTORY_VIEW
        return [HasAction(action)()]

    def get_queryset(self):
        filters = InventoryFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        queryset = InventoryItem.objects.all()
        search = filters.validated_data.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(mining_code__icontains=search) | Q(name__icontains=search)
            )
        if "availability_status" in filters.validated_data:
            queryset = queryset.filter(
                availability_status=filters.validated_data["availability_status"]
            )
        if "is_archived" in filters.validated_data:
            queryset = queryset.filter(
                is_archived=filters.validated_data["is_archived"]
            )
        return queryset

    def create(self, request: Request, *args, **kwargs) -> Response:
        serializer = InventoryItemCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = manage_inventory(
            action="create",
            actor=request.user,
            **serializer.validated_data,
        )
        return Response(
            InventoryItemSerializer(item).data,
            status=status.HTTP_201_CREATED,
        )


class InventoryItemDetailView(generics.GenericAPIView):
    """Read, update, or soft-archive an inventory item."""

    serializer_class = InventoryItemSerializer

    def get_permissions(self):
        action = INVENTORY_VIEW if self.request.method == "GET" else INVENTORY_MANAGE
        return [HasAction(action)()]

    def get_object(self) -> InventoryItem:
        return get_object_or_404(InventoryItem, pk=self.kwargs["item_id"])

    def get(self, request: Request, item_id: int) -> Response:
        return Response(InventoryItemSerializer(self.get_object()).data)

    def put(self, request: Request, item_id: int) -> Response:
        return self._update(request, partial=False)

    def patch(self, request: Request, item_id: int) -> Response:
        return self._update(request, partial=True)

    def _update(self, request: Request, *, partial: bool) -> Response:
        item = self.get_object()
        serializer = InventoryItemUpdateSerializer(data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        item = manage_inventory(
            action="update",
            item=item,
            actor=request.user,
            **serializer.validated_data,
        )
        return Response(InventoryItemSerializer(item).data)

    def delete(self, request: Request, item_id: int) -> Response:
        item = manage_inventory(
            action="archive",
            item=self.get_object(),
            actor=request.user,
        )
        return Response(InventoryItemSerializer(item).data)


class StockAdjustmentView(generics.GenericAPIView):
    """Adjust quantity only through the inventory stock service."""

    permission_classes = [HasAction(INVENTORY_MANAGE)]
    serializer_class = StockAdjustmentSerializer

    def post(self, request: Request, item_id: int) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = get_object_or_404(InventoryItem, pk=item_id)
        item = manage_inventory(
            action="adjust_stock",
            item=item,
            actor=request.user,
            **serializer.validated_data,
        )
        return Response(InventoryItemSerializer(item).data)
