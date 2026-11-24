import random
import re
from collections import Counter
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from activitylog.models import ActivityLog
from inventory.models import InventoryItem
from mining import demo_showcase
from mining.management.commands import demo_detector_tail, demo_live_continuous
from mining.management.commands.demo_live_continuous import (
    choose_demo_comment,
    showcase_index_for_comment,
)
from mining.models import LiveComment


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.django_db(transaction=True)
@override_settings(DEBUG=True)
def test_continuous_rotation_advances_after_exact_comment_count(
    monkeypatch, tmp_path, capsys
):
    from invoicing.tasks import send_invoice

    clock = FakeClock()
    monkeypatch.setattr(demo_live_continuous.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(demo_live_continuous.time, "sleep", clock.sleep)
    monkeypatch.setattr(
        demo_showcase, "SHOWCASE_STATE_PATH", tmp_path / "showcase.json"
    )
    with patch.object(send_invoice, "delay"):
        call_command(
            "demo_live_continuous",
            start_code=100,
            comments_per_item=30,
            duration=31,
            min_gap=1,
            max_gap=1,
            seed=42,
        )

    comments = LiveComment.objects.filter(
        live_video_id__startswith="demo-live-continuous-"
    )
    assert comments.count() == 31
    first_item = InventoryItem.objects.get(mining_code="100")
    assert first_item.stock_qty == 1
    assert Decimal("150.00") <= first_item.selling_price <= Decimal("800.00")
    assert InventoryItem.objects.get(mining_code="101").stock_qty == 1
    assert ActivityLog.objects.filter(
        action="ITEM_CREATED",
        target_type="InventoryItem",
        target_id=str(first_item.pk),
    ).exists()

    output = capsys.readouterr().out
    assert "comment 1/30 for CODE 100" in output
    assert "comment 15/30 for CODE 100" in output
    assert "CODE 100 sold / showcase ended after 30 comments" in output
    assert "comment 1/30 for CODE 101" in output
    assert output.count("[showcasing 100]") == 30
    assert output.count("[showcasing 101]") == 1
    assert "PHP 250.00" in output
    assert "PHP250.00" not in output
    assert "[showcasing101]" not in output


@pytest.mark.django_db(transaction=True)
@override_settings(DEBUG=True)
def test_continuous_command_honors_start_code_and_comments_per_item(
    monkeypatch, tmp_path, capsys
):
    from invoicing.tasks import send_invoice

    clock = FakeClock()
    monkeypatch.setattr(demo_live_continuous.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(demo_live_continuous.time, "sleep", clock.sleep)
    monkeypatch.setattr(
        demo_showcase, "SHOWCASE_STATE_PATH", tmp_path / "showcase.json"
    )
    with patch.object(send_invoice, "delay"):
        call_command(
            "demo_live_continuous",
            start_code=400,
            comments_per_item=2,
            duration=3,
            min_gap=1,
            max_gap=1,
            seed=7,
        )

    assert (
        LiveComment.objects.filter(
            live_video_id__startswith="demo-live-continuous-"
        ).count()
        == 3
    )
    assert InventoryItem.objects.filter(mining_code__in=["400", "401"]).count() == 2
    output = capsys.readouterr().out
    assert output.count("[showcasing 400]") == 2
    assert output.count("[showcasing 401]") == 1


@pytest.mark.django_db(transaction=True)
@override_settings(DEBUG=True)
def test_continuous_demo_recycles_items_with_inventory_archive_service(
    monkeypatch, tmp_path
):
    from invoicing.tasks import send_invoice

    clock = FakeClock()
    monkeypatch.setattr(demo_live_continuous.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(demo_live_continuous.time, "sleep", clock.sleep)
    monkeypatch.setattr(
        demo_showcase, "SHOWCASE_STATE_PATH", tmp_path / "showcase.json"
    )
    with patch.object(send_invoice, "delay"):
        call_command(
            "demo_live_continuous",
            start_code=700,
            comments_per_item=1,
            max_items=1,
            duration=2,
            min_gap=1,
            max_gap=1,
            seed=1,
        )

    assert InventoryItem.objects.get(mining_code="700").is_archived is True
    second = InventoryItem.objects.get(mining_code="701")
    assert second.is_archived is False
    assert ActivityLog.objects.filter(
        action="ITEM_ARCHIVED",
        target_type="InventoryItem",
        target_id=str(InventoryItem.objects.get(mining_code="700").pk),
    ).exists()


@pytest.mark.django_db(transaction=True)
@override_settings(DEBUG=True)
def test_continuous_demo_prints_summary_on_keyboard_interrupt(
    monkeypatch, tmp_path, capsys
):
    from invoicing.tasks import send_invoice

    clock = FakeClock()

    def interrupt_sleep(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(demo_live_continuous.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(
        demo_live_continuous.time,
        "sleep",
        interrupt_sleep,
    )
    monkeypatch.setattr(
        demo_showcase, "SHOWCASE_STATE_PATH", tmp_path / "showcase.json"
    )
    with patch.object(send_invoice, "delay"):
        call_command(
            "demo_live_continuous",
            duration=30,
            min_gap=1,
            max_gap=1,
            seed=6,
        )

    output = capsys.readouterr().out
    assert "showcase ended after 1 of 30 comments" in output
    assert "Demo interrupted: 1 comments" in output


@pytest.mark.django_db
def test_mining_distribution_targets_current_code_about_85_percent():
    rng = random.Random(812)
    counts: Counter[str] = Counter()
    for _ in range(10_000):
        _, message, _ = choose_demo_comment(
            rng,
            current_code=101,
            previous_code=100,
            previous_miner=None,
        )
        match = re.search(r"\d+", message)
        if match:
            if "UNKNOWN-" in message:
                category = "wrong"
            else:
                code = int(match.group())
                category = "current" if code == 101 else "previous"
            counts[category] += 1

    total_mines = sum(counts.values())
    assert counts["current"] / total_mines == pytest.approx(0.85, abs=0.02)
    assert counts["previous"] / total_mines == pytest.approx(0.10, abs=0.02)
    assert counts["wrong"] / total_mines == pytest.approx(0.05, abs=0.02)
    assert showcase_index_for_comment(30, 30) == (1, 1)


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_detector_prints_changed_showcase_banner_and_ignores_missing_file(
    monkeypatch, tmp_path, capsys
):
    state_path = tmp_path / "showcase.json"
    monkeypatch.setattr(demo_showcase, "SHOWCASE_STATE_PATH", state_path)

    call_command("demo_detector_tail", once=True, no_color=True)
    assert "CODE 100 is being showcased" not in capsys.readouterr().out

    demo_showcase.write_showcase_state(
        {
            "code": "100",
            "name": "Vintage denim jacket",
            "price": "350.00",
            "comment_index": 1,
            "comments_per_item": 30,
        }
    )
    reads = 0

    def update_showcase_and_stop(_interval: float) -> None:
        nonlocal reads
        reads += 1
        if reads == 1:
            demo_showcase.write_showcase_state(
                {
                    "code": "101",
                    "name": "Floral summer dress",
                    "price": "250.00",
                    "comment_index": 1,
                    "comments_per_item": 30,
                }
            )
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr(demo_detector_tail.time, "sleep", update_showcase_and_stop)
    call_command("demo_detector_tail", interval=0.1, no_color=True)
    output = capsys.readouterr().out
    assert "CODE 100 is being showcased: Vintage denim jacket - PHP 350.00" in output
    assert "CODE 101 is being showcased: Floral summer dress - PHP 250.00" in output


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_continuous_demo_refuses_to_run_without_debug():
    with pytest.raises(CommandError, match="only when DEBUG is True"):
        call_command("demo_live_continuous")
