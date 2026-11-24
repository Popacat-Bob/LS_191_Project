"""Thin HTTP views for external webhooks."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny

from integrations.services import (
    InvalidWebhookPayload,
    InvalidWebhookSignature,
    handle_facebook_webhook,
    verify_hub_challenge,
)


@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@csrf_exempt
def facebook_webhook(request: HttpRequest) -> HttpResponse:
    """Handle signed webhook deliveries and Meta subscription verification."""
    if request.method == "GET":
        challenge = verify_hub_challenge(
            mode=request.query_params.get("hub.mode"),
            token=request.query_params.get("hub.verify_token"),
            challenge=request.query_params.get("hub.challenge"),
        )
        if challenge is None:
            raise PermissionDenied("Invalid Facebook webhook verification request.")
        return HttpResponse(challenge, content_type="text/plain")

    try:
        handle_facebook_webhook(
            request.body,
            request.headers.get("X-Hub-Signature-256"),
        )
    except InvalidWebhookSignature as exc:
        raise PermissionDenied("Invalid Facebook webhook signature.") from exc
    except InvalidWebhookPayload as exc:
        raise ValidationError(str(exc)) from exc
    return HttpResponse(status=200)
