"""Play the DEBUG-only live-comment demo timeline."""

from __future__ import annotations

import json
import math
import random
import re
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from integrations.tasks import process_comment
from mining.demo_showcase import clear_showcase_state

_BOLD = "\033[1m"
_CYAN = "\033[96m"
_RESET = "\033[0m"
_SHORT_DEMO_CODE = re.compile(r"^(?:mine\s*)?([0-9]{3})$", re.IGNORECASE)


def _expand_demo_short_code(message: str) -> str:
    """Map comments like ``mine 101`` to the seeded DEMO-101 item code."""
    match = _SHORT_DEMO_CODE.fullmatch(message.strip())
    if match is None:
        return message
    prefix = "mine " if message.strip().lower().startswith("mine") else ""
    return f"{prefix}DEMO-{match.group(1)}"


class Command(BaseCommand):
    help = "Play the live comment demo timeline through the mining task."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--speed", type=float, default=1.0)
        parser.add_argument(
            "--jitter",
            nargs="?",
            const=0.25,
            default=0.0,
            type=float,
            help="Add up to this many random delay seconds per comment.",
        )
        parser.add_argument("--seed", type=int, default=0)
        parser.add_argument("--no-reset", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            raise CommandError("demo_live_feed is available only when DEBUG is True.")

        speed = options["speed"]
        jitter = options["jitter"]
        if not math.isfinite(speed) or speed <= 0:
            raise CommandError("--speed must be a positive finite number.")
        if not math.isfinite(jitter) or jitter < 0:
            raise CommandError("--jitter must be a finite non-negative number.")

        timeline = self._load_timeline()
        clear_showcase_state()
        if not options["no_reset"]:
            call_command("demo_reset")

        run_id = uuid4().hex
        live_video_id = f"demo-live-{run_id}"
        rng = random.Random(options["seed"])
        started = time.monotonic()
        previous_at_seconds = -1.0
        for index, event in enumerate(timeline):
            at_seconds = event["at_seconds"]
            if at_seconds < previous_at_seconds:
                raise CommandError("Timeline at_seconds values must be ordered.")
            previous_at_seconds = at_seconds
            due = started + (at_seconds + rng.uniform(0, jitter)) / speed
            time.sleep(max(0.0, due - time.monotonic()))

            sent_at = timezone.now()
            created_time = sent_at - timedelta(
                seconds=event.get("created_time_seconds_before_send", 0)
            )
            fb_comment_id = f"demo-live-{run_id}-{index:03d}"
            data = {
                "fb_comment_id": fb_comment_id,
                "live_video_id": live_video_id,
                "message": _expand_demo_short_code(event["message"]),
                "fb_commenter_id": f"demo-live-{event['name'].casefold()}",
                "fb_commenter_name": event["name"],
                "created_time": created_time.isoformat(),
            }
            result = process_comment.apply(args=(data,), throw=True).get(propagate=True)
            timestamp = timezone.localtime(sent_at).strftime("%H:%M:%S")
            self.stdout.write(
                f"{timestamp}  {_BOLD}{_CYAN}{event['name']}{_RESET}: "
                f"{event['message']}  [{result['outcome']}]",
                ending="\n",
            )
            self.stdout.flush()

    @staticmethod
    def _load_timeline() -> list[dict[str, Any]]:
        path = Path(settings.BASE_DIR) / "scripts" / "demo_timeline.json"
        try:
            timeline = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Could not read demo timeline {path}: {exc}") from exc
        if not isinstance(timeline, list) or not timeline:
            raise CommandError("Demo timeline must be a non-empty JSON list.")

        for index, event in enumerate(timeline):
            if not isinstance(event, dict):
                raise CommandError(f"Timeline event {index} must be an object.")
            allowed = {
                "at_seconds",
                "name",
                "message",
                "created_time_seconds_before_send",
            }
            if set(event) - allowed or not {"at_seconds", "name", "message"} <= set(
                event
            ):
                raise CommandError(f"Timeline event {index} has invalid fields.")
            if (
                isinstance(event["at_seconds"], bool)
                or not isinstance(event["at_seconds"], (int, float))
                or not math.isfinite(event["at_seconds"])
                or event["at_seconds"] < 0
            ):
                raise CommandError(
                    f"Timeline event {index} at_seconds must be non-negative."
                )
            if (
                not isinstance(event["name"], str)
                or not event["name"].strip()
                or not isinstance(event["message"], str)
                or not event["message"].strip()
            ):
                raise CommandError(f"Timeline event {index} needs name and message.")
            backdate = event.get("created_time_seconds_before_send", 0)
            if (
                isinstance(backdate, bool)
                or not isinstance(backdate, (int, float))
                or not math.isfinite(backdate)
                or backdate < 0
            ):
                raise CommandError(
                    f"Timeline event {index} has invalid created-time backdate."
                )
        return timeline
