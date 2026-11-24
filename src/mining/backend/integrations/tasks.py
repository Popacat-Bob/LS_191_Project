"""Celery task entry points for comment processing."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from celery import shared_task
from django.db.utils import InterfaceError, OperationalError
from django.utils import timezone

from integrations.models import WebhookEvent
from integrations.sources import CommentRecord, CommentSource
from mining import services as mining_services
from mining.models import Reservation

logger = logging.getLogger(__name__)


def _record_from_data(data: dict[str, Any]) -> CommentRecord:
    """Rebuild the canonical record from its JSON-safe Celery representation."""
    fields = {
        "fb_comment_id",
        "live_video_id",
        "message",
        "fb_commenter_id",
        "fb_commenter_name",
        "created_time",
    }
    if set(data) != fields:
        raise ValueError(
            "Comment task payload must contain exactly the canonical fields."
        )
    created_time = data["created_time"]
    if not isinstance(created_time, str):
        raise TypeError("created_time must be an ISO-formatted string.")
    record = CommentRecord(
        fb_comment_id=_string_value(data, "fb_comment_id"),
        live_video_id=_string_value(data, "live_video_id"),
        message=_string_value(data, "message"),
        fb_commenter_id=_string_value(data, "fb_commenter_id"),
        fb_commenter_name=_string_value(data, "fb_commenter_name"),
        created_time=datetime.fromisoformat(created_time),
    )
    if timezone.is_naive(record.created_time):
        record = CommentRecord(
            fb_comment_id=record.fb_comment_id,
            live_video_id=record.live_video_id,
            message=record.message,
            fb_commenter_id=record.fb_commenter_id,
            fb_commenter_name=record.fb_commenter_name,
            created_time=timezone.make_aware(record.created_time),
        )
    return record


def _string_value(data: dict[str, Any], key: str) -> str:
    """Validate a string field from the queued comment."""
    value = data[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string.")
    return value


def _result_data(result: object) -> dict[str, Any]:
    """Convert mining service results to JSON-safe task output."""
    if isinstance(result, Reservation):
        return {
            "outcome": "RESERVED",
            "comment_id": result.source_comment.fb_comment_id,
            "reservation_id": result.pk,
        }
    comment = result.comment
    data: dict[str, Any] = {
        "outcome": result.outcome,
        "comment_id": comment.fb_comment_id,
    }
    priority = getattr(result, "priority", None)
    if priority is not None:
        data["priority"] = priority
    return data


@shared_task(
    bind=True,
    autoretry_for=(OperationalError, InterfaceError),
    retry_backoff=True,
    max_retries=5,
    name="integrations.process_comment",
)
def process_comment(
    self: object,
    comment_data: dict[str, Any],
    webhook_event_id: str | None = None,
) -> dict[str, Any]:
    """Process one canonical comment; retry only transient database errors."""
    record = _record_from_data(comment_data)
    result = mining_services.process_comment(
        {
            "fb_comment_id": record.fb_comment_id,
            "live_video_id": record.live_video_id,
            "message": record.message,
            "fb_commenter_id": record.fb_commenter_id,
            "fb_commenter_name": record.fb_commenter_name,
            "created_time": record.created_time,
        }
    )
    if webhook_event_id is not None:
        WebhookEvent.objects.filter(event_id=webhook_event_id).update(
            processed_at=timezone.now()
        )
    return _result_data(result)


def enqueue_record(
    record: CommentRecord, *, webhook_event_id: str | None = None
) -> Any:
    """Enqueue one source record through the shared Celery task."""
    data = {
        "fb_comment_id": record.fb_comment_id,
        "live_video_id": record.live_video_id,
        "message": record.message,
        "fb_commenter_id": record.fb_commenter_id,
        "fb_commenter_name": record.fb_commenter_name,
        "created_time": record.created_time.isoformat(),
    }
    if webhook_event_id is None:
        return process_comment.delay(data)
    return process_comment.delay(data, webhook_event_id=webhook_event_id)


def enqueue_source_comments(
    source: CommentSource, live_video_id: str | None = None
) -> int:
    """Feed every fetched source record through the shared Celery task."""
    count = 0
    for record in source.fetch_comments(live_video_id=live_video_id):
        enqueue_record(record)
        count += 1
    return count
