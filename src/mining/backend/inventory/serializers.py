"""Inventory API serializers."""

from rest_framework import serializers

from inventory.models import InventoryItem

_STOCK_FIELDS = {"stock_qty", "reserved_qty", "availability_status"}


class InventoryItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryItem
        fields = [
            "id",
            "mining_code",
            "name",
            "description",
            "selling_price",
            "acquisition_cost",
            "stock_qty",
            "reserved_qty",
            "availability_status",
            "is_archived",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "reserved_qty",
            "availability_status",
            "updated_at",
        ]


class InventoryItemCreateSerializer(serializers.Serializer):
    mining_code = serializers.CharField(max_length=64)
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True)
    selling_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    acquisition_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    stock_qty = serializers.IntegerField(required=False, min_value=1)

    def to_internal_value(self, data):
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {"non_field_errors": [f"Unknown fields: {', '.join(sorted(unknown))}."]}
            )
        return super().to_internal_value(data)


class InventoryItemUpdateSerializer(serializers.Serializer):
    mining_code = serializers.CharField(max_length=64, required=False)
    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    selling_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False
    )
    acquisition_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False
    )

    def to_internal_value(self, data):
        forbidden = set(data) & _STOCK_FIELDS
        if forbidden:
            raise serializers.ValidationError(
                {
                    field: [
                        "This field can only be changed through the stock workflow."
                    ]
                    for field in sorted(forbidden)
                }
            )
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {"non_field_errors": [f"Unknown fields: {', '.join(sorted(unknown))}."]}
            )
        return super().to_internal_value(data)


class StockAdjustmentSerializer(serializers.Serializer):
    quantity_delta = serializers.IntegerField()


class InventoryFilterSerializer(serializers.Serializer):
    search = serializers.CharField(required=False, allow_blank=True)
    availability_status = serializers.ChoiceField(
        choices=InventoryItem.AvailabilityStatus.choices,
        required=False,
    )
    is_archived = serializers.BooleanField(required=False)
