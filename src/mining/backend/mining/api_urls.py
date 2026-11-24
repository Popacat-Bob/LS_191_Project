"""Mining read API routes."""

from django.urls import path

from mining.api_views import MiningCommentListView

urlpatterns = [
    path("comments/", MiningCommentListView.as_view(), name="mining-comment-list"),
]
