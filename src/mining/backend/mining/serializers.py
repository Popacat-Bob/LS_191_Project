"""Serializers for reservation-management and manual-confirm APIs."""

from rest_framework import serializers

from mining.models import LiveComment, Reservation


class ReservationSerializer(serializers.ModelSerializer):
    """Public reservation fields for the basic list endpoint."""

    item_code = serializers.CharField(source="item.mining_code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    customer_id = serializers.IntegerField(read_only=True)
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    source_comment_id = serializers.CharField(
        source="source_comment.fb_comment_id", read_only=True
    )

    class Meta:
        model = Reservation
        fields = [
            "id",
            "item_code",
            "item_name",
            "customer_id",
            "customer_name",
            "source_comment_id",
            "qty",
            "status",
            "payment_deadline",
            "created_at",
        ]


class StrictInputSerializer(serializers.Serializer):
    """Reject unknown request fields instead of silently ignoring them."""

    def to_internal_value(self, data):
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {"non_field_errors": [f"Unknown fields: {', '.join(sorted(unknown))}."]}
            )
        return super().to_internal_value(data)


class ReservationChangeSerializer(StrictInputSerializer):
    """Accepted fields for manual reservation management."""

    payment_deadline = serializers.DateTimeField(required=False)
    status = serializers.ChoiceField(
        choices=[
            Reservation.Status.CONFIRMED,
            Reservation.Status.CANCELLED,
        ],
        required=False,
    )


class ManualConfirmSerializer(StrictInputSerializer):
    """Optional item code used when manually confirming a comment."""

    mining_code = serializers.CharField(required=False, allow_blank=False)


class ReservationOverrideSerializer(StrictInputSerializer):
    """New customer and audit reason for a SUPER_ADMIN override."""

    new_customer_id = serializers.IntegerField()
    reason = serializers.CharField(trim_whitespace=True, allow_blank=False)


class MineResultSerializer(serializers.Serializer):
    """Small response shape for manual mine confirmation."""

    outcome = serializers.ChoiceField(choices=LiveComment.Outcome.choices)
    comment_id = serializers.CharField()
    reservation_id = serializers.IntegerField(required=False)
    priority = serializers.IntegerField(required=False)
