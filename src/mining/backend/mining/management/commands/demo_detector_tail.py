"""Poll and display outcomes from the DEBUG-only live demo."""

from __future__ import annotations

import time
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import QuerySet
from django.utils import timezone

from mining.demo_showcase import format_showcase_banner, read_showcase_state
from mining.models import BackupMiner, LiveComment, Reservation

_RESET = "\033[0m"
_COLORS = {
    LiveComment.Outcome.RESERVED: "\033[32m",
    LiveComment.Outcome.BACKUP: "\033[33m",
    LiveComment.Outcome.IGNORED: "\033[2m",
    LiveComment.Outcome.INVALID: "\033[31m",
    LiveComment.Outcome.FLAGGED_LATE: "\033[35m",
}


def format_demo_outcome(
    comment: LiveComment, *, item_name: str | None = None, color: bool = True
) -> str:
    """Render one detector result with its item and ranked backup when relevant."""
    outcome = comment.outcome
    if outcome == LiveComment.Outcome.BACKUP:
        label = f"BACKUP #{comment.priority or '-'}"
    else:
        label = outcome
    if outcome in {LiveComment.Outcome.RESERVED, LiveComment.Outcome.BACKUP}:
        label = f"{label} | {item_name or comment.parsed_code or 'Unknown item'}"
    if color:
        label = f"{_COLORS.get(outcome, '')}{label}{_RESET}"
    timestamp = timezone.localtime(comment.created_time).strftime("%H:%M:%S")
    code = comment.parsed_code or "-"
    name = (
        f"\033[1m{comment.fb_commenter_name}\033[0m"
        if color
        else (comment.fb_commenter_name)
    )
    return f"{timestamp} | {name} | {code} | {label}"


def _item_name(comment: LiveComment) -> str | None:
    if comment.outcome == LiveComment.Outcome.RESERVED:
        reservation = (
            Reservation.objects.filter(source_comment=comment)
            .select_related("item")
            .first()
        )
        return reservation.item.name if reservation else None
    if comment.outcome == LiveComment.Outcome.BACKUP:
        backup = (
            BackupMiner.objects.filter(comment=comment).select_related("item").first()
        )
        return backup.item.name if backup else None
    return None


class Command(BaseCommand):
    help = "Tail mining outcomes for the current live demo."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--interval", type=float, default=0.3)
        parser.add_argument("--once", action="store_true", help="Poll once and exit.")

    def handle(self, *args: Any, **options) -> None:
        if not settings.DEBUG:
            raise CommandError(
                "demo_detector_tail is available only when DEBUG is True."
            )
        interval = options["interval"]
        if interval <= 0:
            raise CommandError("--interval must be positive.")

        seen: set[int] = set()
        last_showcase_code: str | None = None
        use_color = not options["no_color"]
        self.stdout.write("Waiting for demo comment outcomes (Ctrl+C to stop)...")
        try:
            while True:
                try:
                    showcase = read_showcase_state()
                except (OSError, ValueError) as exc:
                    raise CommandError(f"Could not read showcase state: {exc}") from exc
                if showcase is None:
                    last_showcase_code = None
                elif str(showcase["code"]) != last_showcase_code:
                    self.stdout.write(format_showcase_banner(showcase, color=use_color))
                    self.stdout.flush()
                    last_showcase_code = str(showcase["code"])

                comments = self._unseen_comments(seen)
                for comment in comments:
                    self.stdout.write(
                        format_demo_outcome(
                            comment,
                            item_name=_item_name(comment),
                            color=use_color,
                        )
                    )
                    self.stdout.flush()
                    seen.add(comment.pk)
                if options["once"]:
                    return
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write("\nDemo detector stopped.")

    @staticmethod
    def _unseen_comments(seen: set[int]) -> QuerySet[LiveComment]:
        queryset = LiveComment.objects.filter(
            live_video_id__startswith="demo-live-"
        ).exclude(outcome="")
        if seen:
            queryset = queryset.exclude(pk__in=seen)
        return queryset.order_by("received_at", "pk")
