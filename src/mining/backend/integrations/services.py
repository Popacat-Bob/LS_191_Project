"""Signature verification and durable webhook ingestion services."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime
from functools import partial
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.permissions import MINING_CONFIRM, can
from integrations.facebook_payload import extract_comment_records
from integrations.models import WebhookEvent
from integrations.sources import CommentRecord
from integrations.tasks import enqueue_record, process_comment

_SIGNATURE_PATTERN = re.compile(r"^sha256=[0-9a-f]{64}$")


class InvalidWebhookSignature(Exception):
    """Raised when a webhook signature is missing or does not validate."""


class InvalidWebhookPayload(Exception):
    """Raised when a correctly signed webhook has invalid JSON or shape."""


def verify_facebook_signature(raw_body: bytes, signature: str | None) -> bool:
    """Verify a Meta signature using its raw-body HMAC-SHA256 digest."""
    secret = settings.FB_APP_SECRET
    if (
        not secret
        or signature is None
        or _SIGNATURE_PATTERN.fullmatch(signature) is None
    ):
        return False
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    return hmac.compare_digest(expected, signature)


def verify_hub_challenge(
    *, mode: str | None, token: str | None, challenge: str | None
) -> str | None:
    """Return a challenge only for a valid subscription verification request."""
    expected_token = settings.FB_VERIFY_TOKEN
    if (
        mode != "subscribe"
        or not expected_token
        or token is None
        or challenge is None
        or not hmac.compare_digest(expected_token, token)
    ):
        return None
    return challenge


def _record_data(record: CommentRecord) -> dict[str, Any]:
    """Serialize a canonical record for JSONField storage."""
    return {
        "fb_comment_id": record.fb_comment_id,
        "live_video_id": record.live_video_id,
        "message": record.message,
        "fb_commenter_id": record.fb_commenter_id,
        "fb_commenter_name": record.fb_commenter_name,
        "created_time": record.created_time.isoformat(),
    }


def record_facebook_comments(payload: object) -> int:
    """Persist unique comment events and enqueue each new record after commit."""
    records = extract_comment_records(payload)
    created_count = 0
    for record in records:
        event_id = f"fb-comment:{record.fb_comment_id}"
        try:
            with transaction.atomic():
                try:
                    with transaction.atomic():
                        event, created = WebhookEvent.objects.get_or_create(
                            event_id=event_id,
                            defaults={
                                "source": WebhookEvent.Source.FACEBOOK,
                                "payload": _record_data(record),
                            },
                        )
                except IntegrityError:
                    if WebhookEvent.objects.filter(event_id=event_id).exists():
                        continue
                    raise

                if created:
                    created_count += 1
                    transaction.on_commit(
                        partial(
                            enqueue_record,
                            record,
                            webhook_event_id=event.event_id,
                        )
                    )
        except IntegrityError:
            if WebhookEvent.objects.filter(event_id=event_id).exists():
                continue
            raise
    return created_count


def handle_facebook_webhook(raw_body: bytes, signature: str | None) -> int:
    """Verify raw request bytes, parse the signed JSON, and store its comments."""
    if not verify_facebook_signature(raw_body, signature):
        raise InvalidWebhookSignature
    try:
        payload = json.loads(raw_body)
        return record_facebook_comments(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise InvalidWebhookPayload(str(exc)) from exc


def _parse_created_time(value: object) -> datetime:
    """Parse an optional ISO timestamp or return the current timestamp."""
    if value is None:
        return timezone.now()
    if not isinstance(value, str):
        raise ValueError("created_time must be an ISO-formatted string.")
    parsed = datetime.fromisoformat(value)
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


def simulate_comment(*, user: object, data: object) -> dict[str, Any]:
    """Authorize and synchronously run one simulated comment through the task."""
    if not can(user, MINING_CONFIRM):
        raise PermissionDenied("You do not have permission to simulate a mine.")
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object.")

    code = data.get("code")
    message = data.get("message")
    if message is None and isinstance(code, (str, int)):
        message = f"mine {code}"
    if not isinstance(message, str) or not message.strip():
        raise ValueError("Provide a non-empty code or message.")
    name = data.get("name", data.get("user"))
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Provide the commenter name in 'name'.")

    commenter_id = data.get("fb_commenter_id") or f"simulated-{uuid4()}"
    video_id = data.get("live_video_id", "simulated-live")
    if not isinstance(commenter_id, str) or not isinstance(video_id, str):
        raise ValueError("fb_commenter_id and live_video_id must be strings.")
    record = CommentRecord(
        fb_comment_id=f"simulated-{uuid4()}",
        live_video_id=video_id,
        message=message,
        fb_commenter_id=commenter_id,
        fb_commenter_name=name.strip(),
        created_time=_parse_created_time(data.get("created_time")),
    )
    task_result = process_comment.apply(
        args=(
            {
                "fb_comment_id": record.fb_comment_id,
                "live_video_id": record.live_video_id,
                "message": record.message,
                "fb_commenter_id": record.fb_commenter_id,
                "fb_commenter_name": record.fb_commenter_name,
                "created_time": record.created_time.isoformat(),
            },
        ),
        throw=True,
    )
    result = task_result.get(propagate=True)
    return {"fb_comment_id": record.fb_comment_id, **result}
