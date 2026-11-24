"""Serializers for mining comment monitoring."""

from rest_framework import serializers

from mining.models import LiveComment


class MiningCommentFilterSerializer(serializers.Serializer):
    live_video_id = serializers.CharField(required=False, allow_blank=False)
    since = serializers.DateTimeField(required=False)
    outcome = serializers.ChoiceField(
        choices=LiveComment.Outcome.choices,
        required=False,
    )


class MiningCommentSerializer(serializers.ModelSerializer):
    class Meta:
        model = LiveComment
        fields = [
            "id",
            "fb_comment_id",
            "live_video_id",
            "message",
            "raw_message",
            "fb_commenter_id",
            "fb_commenter_name",
            "created_time",
            "received_at",
            "parsed_code",
            "outcome",
            "priority",
            "reservation_id",
        ]
