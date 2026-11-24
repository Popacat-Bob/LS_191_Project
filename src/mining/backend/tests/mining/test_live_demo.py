import json
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from inventory.models import InventoryItem
from mining import demo_showcase
from mining.management.commands import demo_live_feed
from mining.management.commands.demo_detector_tail import format_demo_outcome
from mining.models import LiveComment
from tests.factories import LiveCommentFactory


@pytest.mark.django_db
def test_demo_timeline_contains_required_live_events():
    timeline = json.loads(
        (Path(settings.BASE_DIR) / "scripts" / "demo_timeline.json").read_text(
            encoding="utf-8"
        )
    )
    messages = [event["message"].casefold() for event in timeline]
    burst = [
        event["at_seconds"]
        for event in timeline
        if 9 <= event["at_seconds"] <= 10.1
        and event["message"].casefold() in {"mine 101", "mine101", "101"}
    ]

    assert 60 <= timeline[-1]["at_seconds"] <= 90
    assert sum("magkano po" in message for message in messages) >= 2
    assert any("pa-reserve po" in message for message in messages)
    assert any("available pa?" in message for message in messages)
    assert "mine101" in messages
    assert "101" in messages
    assert "mine po 102" in messages
    assert any(message == "mine 999" for message in messages)
    assert burst and max(burst) - min(burst) <= 1.1
    assert any(
        event["name"] == "Juan" and event["message"] == "mine 101" for event in timeline
    )
    assert any(event["message"] == "mine 102" for event in timeline)
    assert any(event["message"] == "mine 103" for event in timeline)
    bea_mines = [
        event["message"]
        for event in timeline
        if event["name"] == "Bea" and event["message"].startswith("mine ")
    ]
    assert bea_mines.index("mine 102") < bea_mines.index("mine 103")
    assert (
        sum(
            event["name"] == "Juan" and event["message"] == "mine 101"
            for event in timeline
        )
        >= 2
    )
    late_events = [
        event for event in timeline if "created_time_seconds_before_send" in event
    ]
    assert len(late_events) == 1


@pytest.mark.django_db(transaction=True)
@override_settings(DEBUG=True, INVOICE_DELAY_SECONDS=0)
def test_live_feed_uses_comment_task_and_produces_all_detector_outcomes(capsys):
    from integrations.tasks import process_comment
    from invoicing.tasks import send_invoice

    original_apply = process_comment.apply
    with (
        patch.object(process_comment, "apply", wraps=original_apply) as apply,
        patch.object(send_invoice, "delay"),
    ):
        call_command(
            "demo_live_feed",
            speed=100_000,
            jitter=0.1,
            seed=23,
        )

    comments = LiveComment.objects.filter(live_video_id__startswith="demo-live-")
    outcomes = set(comments.values_list("outcome", flat=True))
    assert (
        apply.call_count
        == comments.count()
        == len(
            json.loads(
                (Path(settings.BASE_DIR) / "scripts" / "demo_timeline.json").read_text(
                    encoding="utf-8"
                )
            )
        )
    )
    assert {
        LiveComment.Outcome.RESERVED,
        LiveComment.Outcome.BACKUP,
        LiveComment.Outcome.IGNORED,
        LiveComment.Outcome.INVALID,
        LiveComment.Outcome.FLAGGED_LATE,
    } <= outcomes
    assert comments.get(fb_commenter_name="Noel").outcome == LiveComment.Outcome.INVALID
    unrecognized = comments.get(fb_commenter_name="Tita Rose")
    assert unrecognized.outcome not in {
        LiveComment.Outcome.RESERVED,
        LiveComment.Outcome.BACKUP,
    }
    late = comments.get(fb_commenter_name="Late Lani")
    holder = comments.get(
        fb_commenter_name="Juan",
        outcome=LiveComment.Outcome.RESERVED,
    )
    assert late.created_time < holder.created_time
    assert late.outcome == LiveComment.Outcome.FLAGGED_LATE
    assert InventoryItem.objects.get(mining_code="DEMO-101").stock_qty == 1
    output = capsys.readouterr().out
    assert "Juan" in output
    assert "FLAGGED_LATE" in output


@pytest.mark.django_db(transaction=True)
@override_settings(DEBUG=True)
def test_live_feed_clears_stale_showcase_before_processing(monkeypatch, tmp_path):
    state_path = tmp_path / "showcase.json"
    monkeypatch.setattr(demo_showcase, "SHOWCASE_STATE_PATH", state_path)
    state_path.write_text('{"code": "999"}', encoding="utf-8")
    monkeypatch.setattr(
        demo_live_feed.Command,
        "_load_timeline",
        staticmethod(
            lambda: [
                {
                    "at_seconds": 0,
                    "name": "Demo chatter",
                    "message": "just watching",
                }
            ]
        ),
    )

    call_command("demo_live_feed", no_reset=True, speed=100_000)

    assert not state_path.exists()


@pytest.mark.django_db
def test_detector_formats_every_outcome_with_color_and_item_details():
    examples = {
        LiveComment.Outcome.RESERVED: ("\033[32m", "RESERVED | Vintage jacket"),
        LiveComment.Outcome.BACKUP: ("\033[33m", "BACKUP #2 | Vintage jacket"),
        LiveComment.Outcome.IGNORED: ("\033[2m", "IGNORED"),
        LiveComment.Outcome.INVALID: ("\033[31m", "INVALID"),
        LiveComment.Outcome.FLAGGED_LATE: ("\033[35m", "FLAGGED_LATE"),
    }

    for outcome, (color, label) in examples.items():
        comment = LiveCommentFactory(
            fb_commenter_name="Demo Buyer",
            parsed_code="101",
            outcome=outcome,
            priority=2,
        )
        line = format_demo_outcome(
            comment,
            item_name="Vintage jacket",
            color=True,
        )
        assert color in line
        assert "\033[1mDemo Buyer\033[0m" in line
        assert "101" in line
        assert label in line
        assert format_demo_outcome(comment, color=False).find("\033[") == -1


@pytest.mark.django_db
@override_settings(DEBUG=False)
@pytest.mark.parametrize("command", ["demo_live_feed", "demo_detector_tail"])
def test_live_demo_commands_refuse_when_debug_is_off(command):
    with pytest.raises(CommandError, match="only when DEBUG is True"):
        if command == "demo_detector_tail":
            call_command(command, once=True)
        else:
            call_command(command)
