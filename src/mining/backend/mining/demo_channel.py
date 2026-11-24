"""Redis hand-off between the demo commands (say -> chat -> fetch)."""

from __future__ import annotations

import functools
import json
from typing import Any

import redis
from django.conf import settings

LIVE_VIDEO_ID = "demo-live-chat"
YOU_PREFIX = "demo-live-you-"  # commenter ids of comments typed in demo_live_say
CHAT_INBOX = "easylv:demo:chat_inbox"  # demo_live_say -> demo_live_chat
FETCH_INBOX = "easylv:demo:fetch_inbox"  # demo_live_chat -> demo_live_fetch


@functools.cache
def _client() -> redis.Redis:
    return redis.Redis.from_url(settings.CELERY_BROKER_URL)


def clear(*keys: str) -> None:
    """Drop queued comments from a previous session."""
    _client().delete(*keys)


def push(key: str, payload: dict[str, Any]) -> None:
    """Append one canonical comment payload to a queue."""
    _client().rpush(key, json.dumps(payload))


def pop(key: str, *, timeout: float = 0.0) -> dict[str, Any] | None:
    """Take the oldest queued comment, waiting up to ``timeout`` seconds."""
    client = _client()
    if timeout:
        entry = client.blpop(key, timeout=timeout)
        raw = entry[1] if entry else None
    else:
        raw = client.lpop(key)
    return json.loads(raw) if raw is not None else None
