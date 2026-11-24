"""Isolated parsing for incoming Facebook webhook payload shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.utils import timezone

from integrations.sources import CommentRecord


def extract_comment_records(payload: object) -> list[CommentRecord]:
    """Extract normalized comments from the currently supported payload envelope."""
    # TODO(verify): validate this payload envelope and every field mapping against
    # current Meta webhook documentation before enabling a real integration.
    if not isinstance(payload, dict):
        raise ValueError("Webhook payload must be a JSON object.")

    raw_comments: Any = payload.get("comments", [])
    if not isinstance(raw_comments, list):
        raise ValueError("Webhook 'comments' must be a list.")

    records: list[CommentRecord] = []
    for raw in raw_comments:
        if not isinstance(raw, dict):
            raise ValueError("Each webhook comment must be a JSON object.")
        try:
            created_time_value = raw["created_time"]
            if not isinstance(created_time_value, str):
                raise TypeError("created_time must be an ISO-formatted string.")
            created_time = datetime.fromisoformat(created_time_value)
            if timezone.is_naive(created_time):
                created_time = timezone.make_aware(created_time)
            records.append(
                CommentRecord(
                    fb_comment_id=_required_string(raw, "fb_comment_id"),
                    live_video_id=_required_string(raw, "live_video_id"),
                    message=_required_string(raw, "message"),
                    fb_commenter_id=_required_string(raw, "fb_commenter_id"),
                    fb_commenter_name=_required_string(raw, "fb_commenter_name"),
                    created_time=created_time,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid webhook comment: {exc}") from exc
    return records


def _required_string(raw: dict[str, Any], key: str) -> str:
    """Read a non-empty string field from a payload comment."""
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string.")
    return value
