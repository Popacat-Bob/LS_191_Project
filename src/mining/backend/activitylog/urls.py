"""Activity log API routes."""

from django.urls import path

from activitylog.views import ActivityLogListView

urlpatterns = [path("", ActivityLogListView.as_view(), name="activity-log-list")]
