"""Reservation management API URLs."""

from django.urls import path

from mining.views import (
    ReservationDetailView,
    ReservationListView,
    ReservationOverrideView,
)

urlpatterns = [
    path("", ReservationListView.as_view(), name="reservation-list"),
    path(
        "<int:reservation_id>/",
        ReservationDetailView.as_view(),
        name="reservation-detail",
    ),
    path(
        "<int:reservation_id>/override/",
        ReservationOverrideView.as_view(),
        name="reservation-override",
    ),
]
