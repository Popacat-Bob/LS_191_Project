"""Mining API URLs."""

from django.urls import path

from mining.views import ConfirmMineView, simulate_comment_view

urlpatterns = [
    path("comments/simulate/", simulate_comment_view, name="simulate-comment"),
    path(
        "mines/<int:comment_id>/confirm/",
        ConfirmMineView.as_view(),
        name="confirm-mine",
    ),
]
