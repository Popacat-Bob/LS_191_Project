"""DEBUG-only detector: takes relayed comments, ignores or mines them.

Idle until demo_live_chat sends comments. Mines enqueue invoices for the worker.
"""

from __future__ import annotations

import time
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integrations.tasks import process_comment
from mining import demo_channel
from mining.demo_channel import FETCH_INBOX, YOU_PREFIX
from mining.demo_showcase import format_showcase_banner, read_showcase_state
from mining.management.commands.demo_detector_tail import (
    _item_name,
    format_demo_outcome,
)
from mining.models import LiveComment

_BOLD = "\033[1m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"


class Command(BaseCommand):
    help = "Detect relayed live comments (ignore or mine) and hand mines to the worker."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--duration",
            type=float,
            default=0.0,
            help="Stop after this many seconds (default: run until Ctrl+C).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            raise CommandError("demo_live_fetch is available only when DEBUG is True.")
        if options["duration"] < 0:
            raise CommandError("--duration must not be negative.")

        deadline = (
            time.monotonic() + options["duration"] if options["duration"] else None
        )
        last_code: str | None = None
        self.stdout.write(
            "Waiting for comments from `make demo-chat` (Ctrl+C to stop)..."
        )
        try:
            while deadline is None or time.monotonic() < deadline:
                showcase = read_showcase_state()
                if showcase is None:
                    last_code = None
                elif str(showcase["code"]) != last_code:
                    last_code = str(showcase["code"])
                    self.stdout.write(format_showcase_banner(showcase))
                payload = demo_channel.pop(FETCH_INBOX, timeout=0.5)
                if payload is not None:
                    self._detect(payload)
        except KeyboardInterrupt:
            self.stdout.write("\nDetector stopped.")

    def _detect(self, payload: dict[str, Any]) -> None:
        process_comment.apply(args=(payload,), throw=True).get(propagate=True)
        comment = LiveComment.objects.get(fb_comment_id=payload["fb_comment_id"])
        # Detector layout, with the comment text in place of the parsed code.
        parts = format_demo_outcome(comment, item_name=_item_name(comment)).split(
            " | ", 3
        )
        parts[2] = comment.raw_message.strip()
        line = " | ".join(parts)
        if comment.fb_commenter_id.startswith(YOU_PREFIX):
            name = comment.fb_commenter_name
            line = line.replace(
                f"{_BOLD}{name}{_RESET}", f"{_BOLD}{_YELLOW}{name}{_RESET}", 1
            )
            line += f"  {_YELLOW}<- you{_RESET}"
        self.stdout.write(line)
        self.stdout.flush()
