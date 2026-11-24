"""Read-only activity log API serializer."""

from rest_framework import serializers

from activitylog.models import ActivityLog


class ActivityLogFilterSerializer(serializers.Serializer):
    action = serializers.CharField(required=False, allow_blank=False)
    target_type = serializers.CharField(required=False, allow_blank=False)
    target_id = serializers.CharField(required=False, allow_blank=False)
    user = serializers.IntegerField(required=False, min_value=1)
    created_from = serializers.DateTimeField(required=False)
    created_to = serializers.DateTimeField(required=False)

    def validate(self, attrs):
        if (
            attrs.get("created_from") is not None
            and attrs.get("created_to") is not None
            and attrs["created_from"] > attrs["created_to"]
        ):
            raise serializers.ValidationError(
                {"created_from": ["Must be less than or equal to created_to."]}
            )
        return attrs


class ActivityLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActivityLog
        fields = [
            "id",
            "user",
            "action",
            "target_type",
            "target_id",
            "previous_value",
            "new_value",
            "reason",
            "created_at",
        ]
