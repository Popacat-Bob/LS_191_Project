"""DEBUG-only live comment stream: simulated chatter plus demo_live_say comments.

Shows the comment section and relays every comment to demo_live_fetch.
"""

from __future__ import annotations

import math
import random
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from mining import demo_channel
from mining.demo_channel import CHAT_INBOX, FETCH_INBOX, LIVE_VIDEO_ID, YOU_PREFIX
from mining.demo_showcase import (
    clear_showcase_state,
    format_showcase_banner,
    format_showcase_closing,
    write_showcase_state,
)
from mining.management.commands.demo_live_continuous import _CHATTER, _NAMES
from mining.management.commands.demo_live_continuous import (
    Command as ContinuousCommand,
)

_BOLD = "\033[1m"
_CYAN = "\033[96m"
_YELLOW = "\033[93m"
_DIM = "\033[2m"
_RESET = "\033[0m"

# Two-state Markov chain over comment kinds. Its stationary share of "mine"
# comments is 0.08 / (0.08 + 0.72) = 10%; mines come in short clusters.
CHATTER, MINE = "chatter", "mine"
_CHATTER_TO_MINE = 0.08
_MINE_TO_CHATTER = 0.72
_MAX_ITEMS = 50
# Non-claim comments: the shared chatter plus Taglish questions and claim-like
# phrases that carry no item code (one per line in demo_comments.txt).
_COMMENTS = tuple(
    dict.fromkeys(
        [
            *_CHATTER,
            *(
                line.strip()
                for line in (Path(__file__).parents[2] / "demo_comments.txt")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ),
        ]
    )
)


def next_state(rng: random.Random, state: str) -> str:
    """Take one Markov step between chatter and mine comments."""
    if state == CHATTER:
        return MINE if rng.random() < _CHATTER_TO_MINE else CHATTER
    return CHATTER if rng.random() < _MINE_TO_CHATTER else MINE


def comment_for_state(rng: random.Random, state: str, code: int) -> tuple[str, str]:
    """Return a (commenter name, message) pair for the state and showcased code."""
    name = rng.choice(_NAMES)
    if state == CHATTER:
        return name, rng.choice(_COMMENTS)
    return name, rng.choice((f"mine {code}", f"MINE{code}", str(code)))


def comment_payload(*, name: str, commenter_id: str, message: str) -> dict[str, str]:
    """Build the canonical comment payload understood by process_comment."""
    return {
        "fb_comment_id": f"{LIVE_VIDEO_ID}-{uuid4().hex}",
        "live_video_id": LIVE_VIDEO_ID,
        "message": message,
        "fb_commenter_id": commenter_id,
        "fb_commenter_name": name,
        "created_time": timezone.now().isoformat(),
    }


class Command(BaseCommand):
    help = "Stream live comments, show them, and relay them to demo_live_fetch."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--seed", type=int, default=None)
        parser.add_argument(
            "--speed", type=float, default=1.0, help="Divide gaps by this factor."
        )
        parser.add_argument("--min-gap", type=float, default=1.0)
        parser.add_argument("--max-gap", type=float, default=3.0)
        parser.add_argument("--start-code", type=int, default=100)
        parser.add_argument("--comments-per-item", type=int, default=30)
        parser.add_argument(
            "--duration",
            type=float,
            default=0.0,
            help="Stop after this many seconds (default: run until Ctrl+C).",
        )
        parser.add_argument(
            "--no-reset",
            action="store_true",
            help="Keep demo data and any queued comments.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            raise CommandError("demo_live_chat is available only when DEBUG is True.")
        speed = options["speed"]
        if not math.isfinite(speed) or speed <= 0:
            raise CommandError("--speed must be a positive finite number.")
        min_gap, max_gap = options["min_gap"] / speed, options["max_gap"] / speed
        if not (math.isfinite(min_gap) and math.isfinite(max_gap)):
            raise CommandError("Gaps must be finite numbers.")
        if min_gap <= 0 or min_gap > max_gap:
            raise CommandError("Require 0 < --min-gap <= --max-gap.")
        if options["duration"] < 0:
            raise CommandError("--duration must not be negative.")
        if options["comments_per_item"] <= 0 or options["start_code"] < 0:
            raise CommandError("Require --comments-per-item > 0 and --start-code >= 0.")

        clear_showcase_state()
        if not options["no_reset"]:
            call_command("demo_reset")
            demo_channel.clear(CHAT_INBOX, FETCH_INBOX)

        rng = random.Random(options["seed"])
        self._sent = 0
        self._per_item = options["comments_per_item"]
        self._start_code = options["start_code"]
        self._offset = -1
        self._item = None
        self._items = ContinuousCommand()

        deadline = (
            time.monotonic() + options["duration"] if options["duration"] else None
        )
        state = CHATTER
        try:
            self._sync_showcase()
            while True:
                next_at = time.monotonic() + rng.uniform(min_gap, max_gap)
                while time.monotonic() < next_at:
                    if deadline is not None and time.monotonic() >= deadline:
                        return
                    typed = demo_channel.pop(CHAT_INBOX)
                    if typed is None:
                        time.sleep(0.05)
                    else:
                        self._publish(typed)
                state = next_state(rng, state)
                name, message = comment_for_state(rng, state, self._code)
                self._publish(
                    comment_payload(
                        name=name,
                        commenter_id=f"demo-live-{name.casefold()}",
                        message=message,
                    )
                )
        except KeyboardInterrupt:
            self.stdout.write("\nLive chat stopped.")

    @property
    def _code(self) -> int:
        return self._start_code + self._offset

    def _publish(self, payload: dict[str, Any]) -> None:
        """Show one comment in the chat and relay it to the detector."""
        own = payload["fb_commenter_id"].startswith(YOU_PREFIX)
        color = _YELLOW if own else _CYAN
        self.stdout.write(
            f"{timezone.localtime():%H:%M:%S}  "
            f"{_BOLD}{color}{payload['fb_commenter_name']}{_RESET}: "
            f"{payload['message'].strip()}"
        )
        self.stdout.flush()
        demo_channel.push(FETCH_INBOX, payload)
        self._sent += 1
        self._sync_showcase()

    def _state(self) -> dict[str, Any]:
        return ContinuousCommand._showcase_state(
            self._item, self._sent % self._per_item + 1, self._per_item
        )

    def _sync_showcase(self) -> None:
        """Advance to the next showcased item after each block of comments."""
        offset = self._sent // self._per_item
        if offset != self._offset:
            if self._item is not None:
                self.stdout.write(
                    format_showcase_closing(
                        str(self._code), self._per_item, self._per_item
                    )
                )
            self._offset = offset
            self._item = self._items._ensure_item(
                self._code, start_code=self._start_code, max_items=_MAX_ITEMS
            )
            self.stdout.write(format_showcase_banner(self._state()))
            self.stdout.write(
                f"{_DIM}To claim it, comment: mine {self._code}  "
                f"(run `make demo-say` in another terminal){_RESET}\n"
            )
            self.stdout.flush()
        write_showcase_state(self._state())
