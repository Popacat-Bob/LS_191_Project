"""Play a DEBUG-only continuous comment stream with sequential showcases."""

from __future__ import annotations

import math
import random
import time
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.test import override_settings
from django.utils import timezone

from integrations.tasks import process_comment
from inventory.models import InventoryItem
from inventory.services import manage_inventory
from mining.demo_showcase import (
    DEMO_NAME_PREFIX,
    clear_showcase_state,
    format_showcase_banner,
    format_showcase_closing,
    format_showcase_tag,
    write_showcase_state,
)

_RESET = "\033[0m"
_BOLD = "\033[1m"
_CYAN = "\033[96m"

_ITEMS = (
    ("Vintage denim jacket", Decimal("350.00")),
    ("Floral summer dress", Decimal("250.00")),
    ("Leather crossbody bag", Decimal("500.00")),
    ("Classic linen shirt", Decimal("300.00")),
    ("Embroidered blouse", Decimal("425.00")),
    ("Canvas tote bag", Decimal("180.00")),
    ("Pleated midi skirt", Decimal("375.00")),
    ("Retro windbreaker", Decimal("650.00")),
    ("Knit cardigan", Decimal("550.00")),
    ("Suede ankle boots", Decimal("800.00")),
)
_NAMES = (
    "Ate May",
    "Bea",
    "Cathy",
    "Dina",
    "Ella",
    "Joy",
    "Karen",
    "Liza",
    "Mia",
    "Nica",
    "Tina",
    "Ysa",
)
_CHATTER = (
    "Magkano po?",
    "Available pa?",
    "Ang ganda nito 😍",
    "Pa-reserve po 🥹",
    "Hm sis?",
    "Mine po ba ito?",
    "Love this! ❤️",
)


def showcase_index_for_comment(
    comments_sent: int, comments_per_item: int
) -> tuple[int, int]:
    """Return zero-based item offset and one-based position for the next comment."""
    return divmod(comments_sent, comments_per_item)[0], (
        comments_sent % comments_per_item
    ) + 1


def choose_demo_comment(
    rng: random.Random,
    *,
    current_code: int,
    previous_code: int | None,
    previous_miner: str | None,
) -> tuple[str, str, bool]:
    """Pick chatter or a mine, with the requested split among mine targets."""
    name = (
        previous_miner
        if previous_miner is not None and rng.random() < 0.18
        else rng.choice(_NAMES)
    )
    if rng.random() < 0.25:
        return name, rng.choice(_CHATTER), False

    target_roll = rng.random()
    if target_roll < 0.85 or previous_code is None:
        code = current_code
        backdated = False
    elif target_roll < 0.95:
        code = previous_code
        backdated = True
    else:
        code = f"UNKNOWN-{current_code}-{rng.randint(100_000, 999_999)}"
        backdated = False
    message = rng.choice((f"mine {code}", f"MINE{code}", str(code)))
    return name, message, backdated


class Command(BaseCommand):
    help = "Play a continuous DEBUG-only comment stream with item showcases."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--speed", type=float, default=1.0)
        parser.add_argument("--min-gap", type=float, default=1.0)
        parser.add_argument("--max-gap", type=float, default=3.0)
        parser.add_argument("--start-code", type=int, default=100)
        parser.add_argument("--comments-per-item", type=int, default=30)
        parser.add_argument("--max-items", type=int, default=50)
        parser.add_argument("--reservation-ttl-seconds", type=float, default=45.0)
        parser.add_argument("--duration", type=float, default=180.0)
        parser.add_argument("--no-reset", action="store_true")

    def handle(self, *args: Any, **options) -> None:
        if not settings.DEBUG:
            raise CommandError(
                "demo_live_continuous is available only when DEBUG is True."
            )
        self._validate_options(options)

        clear_showcase_state()
        if not options["no_reset"]:
            call_command("demo_reset")

        rng = random.Random(options["seed"])
        run_id = uuid4().hex
        live_video_id = f"demo-live-continuous-{run_id}"
        started = time.monotonic()
        next_burst_at = rng.uniform(8.0, 15.0)
        burst_remaining = 0
        previous_miner: str | None = None
        comments_sent = 0
        outcomes: Counter[str] = Counter()
        current_item: InventoryItem | None = None
        current_code: int | None = None
        current_showcase_count = 0
        showcase_closed = False
        use_color = not options["no_color"]
        interrupted = False

        try:
            while time.monotonic() - started < options["duration"]:
                elapsed = time.monotonic() - started
                if comments_sent:
                    gap = rng.uniform(options["min_gap"], options["max_gap"])
                    gap /= options["speed"]
                    remaining = options["duration"] - elapsed
                    if gap > remaining:
                        time.sleep(max(0.0, remaining))
                        break
                    time.sleep(gap)
                    if time.monotonic() - started >= options["duration"]:
                        break

                item_offset, comment_index = showcase_index_for_comment(
                    comments_sent,
                    options["comments_per_item"],
                )
                code = options["start_code"] + item_offset
                if code != current_code:
                    if current_code is not None:
                        if not showcase_closed:
                            self._close_showcase(
                                current_code,
                                current_showcase_count,
                                options["comments_per_item"],
                                color=use_color,
                            )
                    current_item = self._ensure_item(
                        code,
                        start_code=options["start_code"],
                        max_items=options["max_items"],
                    )
                    current_code = code
                    current_showcase_count = 0
                    showcase_closed = False

                assert current_item is not None and current_code is not None
                state = self._showcase_state(
                    current_item,
                    comment_index,
                    options["comments_per_item"],
                )
                if comment_index == 1:
                    self._write_banner(state, color=use_color)

                elapsed = time.monotonic() - started
                if burst_remaining == 0 and elapsed >= next_burst_at:
                    burst_remaining = rng.randint(3, 4)
                    next_burst_at = elapsed + rng.uniform(8.0, 15.0)
                if burst_remaining:
                    name = rng.choice(_NAMES)
                    message = f"mine {current_code}"
                    backdated = False
                    burst_remaining -= 1
                else:
                    previous_code = current_code - 1 if item_offset > 0 else None
                    name, message, backdated = choose_demo_comment(
                        rng,
                        current_code=current_code,
                        previous_code=previous_code,
                        previous_miner=previous_miner,
                    )
                is_mine = message.strip().lower().startswith("mine")
                is_bare_code = message.strip().isdigit()
                if is_mine or is_bare_code:
                    previous_miner = name

                sent_at = timezone.now()
                created_time = sent_at - (
                    timedelta(minutes=10) if backdated else timedelta()
                )
                fb_comment_id = f"{live_video_id}-{comments_sent:06d}"
                payload = {
                    "fb_comment_id": fb_comment_id,
                    "live_video_id": live_video_id,
                    "message": message,
                    "fb_commenter_id": f"demo-continuous-{name.casefold()}",
                    "fb_commenter_name": name,
                    "created_time": created_time.isoformat(),
                }
                write_showcase_state(state)
                with override_settings(
                    PAYMENT_WINDOW_HOURS=options["reservation_ttl_seconds"] / 3600
                ):
                    result = process_comment.apply(args=(payload,), throw=True).get(
                        propagate=True
                    )
                outcomes[result["outcome"]] += 1
                current_showcase_count += 1
                comments_sent += 1
                tag = format_showcase_tag(str(current_code), color=use_color)
                timestamp = timezone.localtime(sent_at).strftime("%H:%M:%S")
                commenter = f"{_BOLD}{_CYAN}{name}{_RESET}" if use_color else name
                self.stdout.write(
                    f"{timestamp}  {commenter}: {message}  [{result['outcome']}]  {tag}"
                )
                self.stdout.flush()

                if (
                    current_showcase_count == (options["comments_per_item"] + 1) // 2
                    and options["comments_per_item"] > 1
                ):
                    midpoint_state = self._showcase_state(
                        current_item,
                        comment_index=current_showcase_count,
                        comments_per_item=options["comments_per_item"],
                    )
                    self.stdout.write(
                        format_showcase_banner(midpoint_state, color=use_color)
                    )
                    self.stdout.flush()

                write_showcase_state(
                    self._showcase_state(
                        current_item,
                        comment_index=min(
                            current_showcase_count,
                            options["comments_per_item"],
                        ),
                        comments_per_item=options["comments_per_item"],
                    )
                )
                if current_showcase_count == options["comments_per_item"]:
                    self._close_showcase(
                        current_code,
                        current_showcase_count,
                        options["comments_per_item"],
                        color=use_color,
                    )
                    showcase_closed = True
            if (
                current_code is not None
                and current_showcase_count
                and not showcase_closed
            ):
                self._close_showcase(
                    current_code,
                    current_showcase_count,
                    options["comments_per_item"],
                    color=use_color,
                )
                showcase_closed = True
        except KeyboardInterrupt:
            interrupted = True
            self.stdout.write("")
            if (
                current_code is not None
                and current_showcase_count
                and not showcase_closed
            ):
                self._close_showcase(
                    current_code,
                    current_showcase_count,
                    options["comments_per_item"],
                    color=use_color,
                )
                showcase_closed = True
        self.stdout.write(
            f"Demo {'interrupted' if interrupted else 'finished'}: "
            f"{comments_sent} comments; "
            + ", ".join(
                f"{outcome}={count}" for outcome, count in sorted(outcomes.items())
            )
        )

    @staticmethod
    def _validate_options(options: dict[str, Any]) -> None:
        for name in (
            "speed",
            "min_gap",
            "max_gap",
            "reservation_ttl_seconds",
            "duration",
        ):
            value = options[name]
            if not math.isfinite(value) or value <= 0:
                raise CommandError(f"--{name.replace('_', '-')} must be positive.")
        if options["min_gap"] > options["max_gap"]:
            raise CommandError("--min-gap must be less than or equal to --max-gap.")
        for name in ("comments_per_item", "max_items"):
            if type(options[name]) is not int or options[name] <= 0:
                raise CommandError(
                    f"--{name.replace('_', '-')} must be a positive integer."
                )
        if type(options["start_code"]) is not int or options["start_code"] < 0:
            raise CommandError("--start-code must be a non-negative integer.")

    @staticmethod
    def _showcase_state(
        item: InventoryItem,
        comment_index: int,
        comments_per_item: int,
    ) -> dict[str, Any]:
        return {
            "code": item.mining_code,
            "name": item.name.removeprefix(DEMO_NAME_PREFIX),
            "price": f"{item.selling_price:.2f}",
            "comment_index": comment_index,
            "comments_per_item": comments_per_item,
        }

    def _write_banner(self, state: dict[str, Any], *, color: bool) -> None:
        self.stdout.write(format_showcase_banner(state, color=color))
        self.stdout.flush()

    def _close_showcase(
        self,
        code: int,
        count: int,
        expected_count: int,
        *,
        color: bool,
    ) -> None:
        self.stdout.write(
            format_showcase_closing(
                str(code),
                count,
                expected_count,
                color=color,
            )
        )
        self.stdout.write("")
        self.stdout.flush()

    def _ensure_item(
        self,
        code: int,
        *,
        start_code: int,
        max_items: int,
    ) -> InventoryItem:
        code_value = str(code)
        existing = InventoryItem.objects.filter(
            mining_code=code_value,
            is_archived=False,
        ).first()
        if existing is not None:
            if not existing.name.startswith(DEMO_NAME_PREFIX):
                raise CommandError(
                    f"Mining code {code_value} belongs to a non-demo inventory item."
                )
            if existing.availability_status == InventoryItem.AvailabilityStatus.SOLD:
                manage_inventory(
                    action="archive",
                    item=existing,
                    reason="Recycled by the continuous live demo.",
                )
            else:
                return existing

        active_demo_items = [
            item
            for item in InventoryItem.objects.filter(
                is_archived=False,
                name__startswith=DEMO_NAME_PREFIX,
            ).order_by("pk")
            if item.mining_code.isdecimal()
        ]
        while len(active_demo_items) >= max_items:
            recyclable = active_demo_items[0]
            manage_inventory(
                action="archive",
                item=recyclable,
                reason="Recycled by the continuous live demo item limit.",
            )
            active_demo_items.pop(0)

        name, price = _ITEMS[(code - start_code) % len(_ITEMS)]
        return manage_inventory(
            action="create",
            mining_code=code_value,
            name=f"{DEMO_NAME_PREFIX}{name}",
            description="Continuous live demo inventory item.",
            selling_price=price,
            acquisition_cost=Decimal("0.00"),
            stock_qty=1,
            reason="Created by the continuous live demo.",
        )
