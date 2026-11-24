"""Shared REST API pagination and error formatting."""

from __future__ import annotations

from typing import Any

from rest_framework import exceptions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import exception_handler as default_exception_handler


class DefaultPagination(PageNumberPagination):
    """Project-wide page-number pagination, capped at 100 results per page."""

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def _domain_exception(exc: Exception) -> tuple[int, str] | None:
    """Map service-layer domain exceptions to stable API status/code pairs."""
    from inventory.services import (
        InsufficientReservedStock,
        InsufficientStock,
        InvalidInventoryAction,
        InvalidInventoryState,
        InvalidQuantity,
    )
    from invoicing.services import InvoiceOrderNotFound, InvoiceSkipped
    from mining.services import (
        ReservationConflict,
        ReservationNotFound,
        ReservationValidationError,
    )

    if isinstance(
        exc,
        (InsufficientStock, InsufficientReservedStock, ReservationConflict),
    ):
        return status.HTTP_409_CONFLICT, "conflict"
    if isinstance(
        exc,
        (
            InvalidInventoryAction,
            InvalidInventoryState,
            InvalidQuantity,
            ReservationValidationError,
        ),
    ):
        return status.HTTP_400_BAD_REQUEST, "validation_error"
    if isinstance(exc, (ReservationNotFound, InvoiceOrderNotFound)):
        return status.HTTP_404_NOT_FOUND, "not_found"
    if isinstance(exc, InvoiceSkipped):
        return status.HTTP_409_CONFLICT, "invoice_skipped"
    if isinstance(exc, ValueError):
        return status.HTTP_400_BAD_REQUEST, "validation_error"
    return None


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Return all REST errors using one predictable JSON envelope."""
    mapped = _domain_exception(exc)
    if mapped is None:
        response = default_exception_handler(exc, context)
        if response is None:
            return None
        response_data = response.data
        if isinstance(exc, exceptions.ValidationError):
            code = "validation_error"
        elif isinstance(exc, exceptions.NotAuthenticated):
            code = "not_authenticated"
        elif isinstance(exc, exceptions.PermissionDenied):
            code = "permission_denied"
        else:
            detail_value = (
                response_data.get("detail") if isinstance(response_data, dict) else None
            )
            code = getattr(detail_value, "code", "api_error")
        if isinstance(response_data, dict) and "detail" not in response_data:
            detail = "Request validation failed."
            fields = response_data
        else:
            detail = (
                str(response_data.get("detail", response_data))
                if isinstance(response_data, dict)
                else str(response_data)
            )
            fields = None
        response.data = {
            "error": {
                "code": str(code),
                "detail": detail,
                **({"fields": fields} if fields is not None else {}),
            }
        }
        return response

    http_status, code = mapped
    detail = str(exc)
    return Response(
        {"error": {"code": code, "detail": detail}},
        status=http_status,
    )
