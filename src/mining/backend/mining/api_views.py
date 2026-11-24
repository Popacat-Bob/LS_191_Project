"""Read-only API for monitoring ingested mining comments."""

from rest_framework import generics

from accounts.permissions import MINING_MONITOR, HasAction
from mining.api_serializers import (
    MiningCommentFilterSerializer,
    MiningCommentSerializer,
)
from mining.models import LiveComment


class MiningCommentListView(generics.ListAPIView):
    """List recent comments with basic stream, time, and outcome filters."""

    serializer_class = MiningCommentSerializer
    permission_classes = [HasAction(MINING_MONITOR)]

    def get_queryset(self):
        filters = MiningCommentFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        queryset = LiveComment.objects.all().order_by("-created_time", "-pk")
        values = filters.validated_data
        if "live_video_id" in values:
            queryset = queryset.filter(live_video_id=values["live_video_id"])
        if "since" in values:
            queryset = queryset.filter(created_time__gte=values["since"])
        if "outcome" in values:
            queryset = queryset.filter(outcome=values["outcome"])
        return queryset
