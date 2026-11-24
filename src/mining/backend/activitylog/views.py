"""Read-only, SUPER_ADMIN activity log endpoint."""

from rest_framework import generics

from accounts.permissions import ACTIVITYLOG_VIEW, HasAction
from activitylog.models import ActivityLog
from activitylog.serializers import ActivityLogFilterSerializer, ActivityLogSerializer


class ActivityLogListView(generics.ListAPIView):
    serializer_class = ActivityLogSerializer
    permission_classes = [HasAction(ACTIVITYLOG_VIEW)]

    def get_queryset(self):
        filters = ActivityLogFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        queryset = ActivityLog.objects.select_related("user").all()
        values = filters.validated_data
        if "action" in values:
            queryset = queryset.filter(action=values["action"])
        if "target_type" in values:
            queryset = queryset.filter(target_type=values["target_type"])
        if "target_id" in values:
            queryset = queryset.filter(target_id=values["target_id"])
        if "user" in values:
            queryset = queryset.filter(user_id=values["user"])
        if "created_from" in values:
            queryset = queryset.filter(created_at__gte=values["created_from"])
        if "created_to" in values:
            queryset = queryset.filter(created_at__lte=values["created_to"])
        return queryset
