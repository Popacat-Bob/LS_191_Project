"""Pluggable, offline-first sources of live comment records."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from queue import Empty, Queue
from typing import Protocol

from django.core.exceptions import ImproperlyConfigured


@dataclass(frozen=True)
class CommentRecord:
    """Canonical comment record shared by comment source adapters."""

    fb_comment_id: str
    live_video_id: str
    message: str
    fb_commenter_id: str
    fb_commenter_name: str
    created_time: datetime


class CommentSource(Protocol):
    """Interface implemented by sources that yield canonical comments."""

    def fetch_comments(
        self, live_video_id: str | None = None
    ) -> Iterable[CommentRecord]:
        """Yield comments, optionally restricted to one live video."""
        ...


class SimulatedCommentSource:
    """Thread-safe in-memory source for local development and tests."""

    def __init__(self) -> None:
        self._comments: Queue[CommentRecord] = Queue()

    def publish(self, comment: CommentRecord) -> None:
        """Add a simulated comment to this source."""
        self._comments.put(comment)

    def fetch_comments(
        self, live_video_id: str | None = None
    ) -> Iterable[CommentRecord]:
        """Drain currently queued comments, retaining records for other videos."""
        retained: list[CommentRecord] = []
        while True:
            try:
                record = self._comments.get_nowait()
            except Empty:
                break
            if live_video_id is None or record.live_video_id == live_video_id:
                yield record
            else:
                retained.append(record)
        for record in retained:
            self._comments.put(record)


class GraphApiCommentSource:
    """Deferred placeholder for a future real Facebook comment source."""

    def fetch_comments(
        self, live_video_id: str | None = None
    ) -> Iterable[CommentRecord]:
        # TODO(verify): configure the Facebook Page and Facebook App.
        # TODO(verify): verify the required Page permissions.
        # TODO(verify): verify webhook subscription for live comments.
        # TODO(verify): verify polling fallback and stored cursor behavior.
        raise NotImplementedError("Graph API is deferred")


_SIMULATED_SOURCE = SimulatedCommentSource()


def get_comment_source() -> CommentSource:
    """Return the source selected by ``COMMENT_SOURCE``."""
    from django.conf import settings

    source_name = settings.COMMENT_SOURCE
    if source_name == "simulated":
        return _SIMULATED_SOURCE
    if source_name == "graph_api":
        return GraphApiCommentSource()
    raise ImproperlyConfigured(f"Unknown COMMENT_SOURCE: {source_name!r}.")
