"""Thin mining API views."""

from __future__ import annotations

from django.conf import settings
from django.http import Http404
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.permissions import (
    MINING_CONFIRM,
    RESERVATIONS_MANAGE,
    RESERVATIONS_OVERRIDE,
    RESERVATIONS_VIEW,
    HasAction,
)
from integrations.services import simulate_comment
from mining.expiry import manage_reservation
from mining.models import Reservation
from mining.serializers import (
    ManualConfirmSerializer,
    MineResultSerializer,
    ReservationChangeSerializer,
    ReservationOverrideSerializer,
    ReservationSerializer,
)
from mining.services import (
    BackupPosition,
    confirm_comment,
    override_reservation,
)


class DebugOnlyMiningConfirm(HasAction(MINING_CONFIRM)):
    """Hide the simulator when debugging is disabled, then enforce its action."""

    def has_permission(self, request: Request, view: object) -> bool:
        if not settings.DEBUG:
            raise Http404
        return super().has_permission(request, view)


@api_view(["POST"])
@permission_classes([DebugOnlyMiningConfirm])
def simulate_comment_view(request: Request) -> Response:
    """Run a development-only simulated mine through the shared task."""
    if not settings.DEBUG:
        raise Http404
    result = simulate_comment(user=request.user, data=request.data)
    return Response(result, status=status.HTTP_200_OK)


class ReservationListView(generics.ListAPIView):
    """Paginated reservation listing with basic status/item/customer filters."""

    serializer_class = ReservationSerializer
    permission_classes = [HasAction(RESERVATIONS_VIEW)]

    def get_queryset(self):
        queryset = Reservation.objects.select_related(
            "item", "customer", "source_comment"
        ).order_by("pk")
        status_filter = self.request.query_params.get("status")
        item_code = self.request.query_params.get("item_code")
        customer = self.request.query_params.get("customer")
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())
        if item_code:
            queryset = queryset.filter(item__mining_code__iexact=item_code.strip())
        if customer:
            if customer.isdecimal():
                queryset = queryset.filter(customer_id=int(customer))
            else:
                queryset = queryset.filter(customer__facebook_id=customer)
        return queryset


class ReservationDetailView(generics.GenericAPIView):
    """Update an active reservation's deadline or status."""

    permission_classes = [HasAction(RESERVATIONS_MANAGE)]
    serializer_class = ReservationChangeSerializer

    def patch(self, request: Request, reservation_id: int) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reservation = manage_reservation(
            reservation_id,
            changes=serializer.validated_data,
            actor=request.user,
        )
        return Response(ReservationSerializer(reservation).data)


class ConfirmMineView(generics.GenericAPIView):
    """Manually send an ignored/invalid comment through the shared claim path."""

    permission_classes = [HasAction(MINING_CONFIRM)]
    serializer_class = ManualConfirmSerializer

    def post(self, request: Request, comment_id: int) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = confirm_comment(
            comment_id,
            mining_code=serializer.validated_data.get("mining_code"),
            actor=request.user,
        )
        comment = (
            result.comment
            if isinstance(result, BackupPosition)
            else result.source_comment
        )
        data = {
            "outcome": comment.outcome,
            "comment_id": comment.fb_comment_id,
            "reservation_id": comment.reservation_id,
            "priority": comment.priority,
        }
        return Response(MineResultSerializer(data).data)


class ReservationOverrideView(generics.GenericAPIView):
    """Transfer an active/confirmed reservation; SUPER_ADMIN only."""

    permission_classes = [HasAction(RESERVATIONS_OVERRIDE)]
    serializer_class = ReservationOverrideSerializer

    def post(self, request: Request, reservation_id: int) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_reservation, _old_order, new_order = override_reservation(
            reservation_id,
            new_customer_id=serializer.validated_data["new_customer_id"],
            reason=serializer.validated_data["reason"],
            actor=request.user,
        )
        return Response(
            {
                "reservation_id": new_reservation.pk,
                "customer_id": new_reservation.customer_id,
                "status": new_reservation.status,
                "order_id": new_order.pk,
            }
        )
