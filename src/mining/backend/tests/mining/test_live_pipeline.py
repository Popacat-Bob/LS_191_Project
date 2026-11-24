import random
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from mining import demo_channel, demo_showcase
from mining.management.commands.demo_live_chat import (
    CHATTER,
    MINE,
    comment_for_state,
    comment_payload,
    next_state,
)
from mining.models import LiveComment
from tests.factories import InventoryItemFactory


class FakeRedis:
    def __init__(self) -> None:
        self.queues: dict[str, list[str]] = {}

    def rpush(self, key, value):
        self.queues.setdefault(key, []).append(value)

    def lpop(self, key):
        queue = self.queues.get(key)
        return queue.pop(0) if queue else None

    def blpop(self, key, timeout=0):
        value = self.lpop(key)
        return (key, value) if value is not None else None

    def delete(self, *keys):
        for key in keys:
            self.queues.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch, tmp_path):
    fake = FakeRedis()
    monkeypatch.setattr(demo_channel, "_client", lambda: fake)
    monkeypatch.setattr(demo_showcase, "SHOWCASE_STATE_PATH", tmp_path / "state.json")
    return fake


def _item():
    return InventoryItemFactory(mining_code="100", name="[DEMO] Test jacket")


def test_markov_chain_yields_about_ten_percent_mine_comments():
    rng = random.Random(5)
    state = CHATTER
    mines = 0
    steps = 50_000
    for _ in range(steps):
        state = next_state(rng, state)
        mines += state == MINE

    assert mines / steps == pytest.approx(0.10, abs=0.01)


def test_comment_for_state_matches_state():
    _, mine_message = comment_for_state(random.Random(1), MINE, 105)
    assert "105" in mine_message


def test_comment_pool_has_no_accidental_item_claims():
    from mining.management.commands.demo_live_chat import _COMMENTS
    from mining.services import normalize_message, parse_code

    assert len(_COMMENTS) > 300
    assert "dibs" in _COMMENTS and "mine is broken lol" in _COMMENTS
    claimed = {parse_code(normalize_message(text)) for text in _COMMENTS}
    assert not claimed & {str(code) for code in range(100, 1000)}


@override_settings(DEBUG=True)
def test_say_sends_comment_to_chat_inbox(fake_redis):
    with patch("builtins.input", side_effect=["mine 100", EOFError]):
        call_command("demo_live_say", name="Tester")

    payload = demo_channel.pop(demo_channel.CHAT_INBOX)
    assert payload["message"] == "mine 100"
    assert payload["fb_commenter_name"] == "Tester"
    assert payload["fb_commenter_id"].startswith(demo_channel.YOU_PREFIX)
    assert demo_channel.pop(demo_channel.FETCH_INBOX) is None


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_chat_shows_and_relays_comments_without_detecting(fake_redis, capsys):
    _item()
    demo_channel.push(
        demo_channel.CHAT_INBOX,
        comment_payload(
            name="Tester",
            commenter_id=f"{demo_channel.YOU_PREFIX}tester",
            message="mine 100",
        ),
    )

    call_command(
        "demo_live_chat",
        no_reset=True,
        duration=0.4,
        min_gap=0.1,
        max_gap=0.2,
        seed=3,
    )

    out = capsys.readouterr().out
    assert "is being showcased" in out
    assert "\033[1m\033[93mTester\033[0m: mine 100" in out
    relayed = []
    while (payload := demo_channel.pop(demo_channel.FETCH_INBOX)) is not None:
        relayed.append(payload)
    assert any(p["message"] == "mine 100" for p in relayed)
    assert LiveComment.objects.count() == 0  # detection is demo_live_fetch's job


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_fetch_detects_relayed_comments_and_stays_idle_otherwise(fake_redis, capsys):
    from invoicing.tasks import send_invoice

    _item()
    call_command("demo_live_fetch", duration=0.2)
    assert LiveComment.objects.count() == 0

    demo_channel.push(
        demo_channel.FETCH_INBOX,
        comment_payload(
            name="Tester",
            commenter_id=f"{demo_channel.YOU_PREFIX}tester",
            message="mine 100",
        ),
    )
    demo_channel.push(
        demo_channel.FETCH_INBOX,
        comment_payload(name="Bea", commenter_id="demo-live-bea", message="Magkano?"),
    )
    with patch.object(send_invoice, "delay"):
        call_command("demo_live_fetch", duration=0.5)

    outcomes = dict(LiveComment.objects.values_list("fb_commenter_name", "outcome"))
    assert outcomes == {"Tester": "RESERVED", "Bea": "IGNORED"}
    out = capsys.readouterr().out
    assert "RESERVED | [DEMO] Test jacket" in out
    assert "Magkano?" in out
    assert "<- you" in out


@pytest.mark.django_db
@override_settings(DEBUG=False)
@pytest.mark.parametrize(
    "command", ["demo_live_chat", "demo_live_fetch", "demo_live_say"]
)
def test_demo_commands_refuse_without_debug(command):
    with pytest.raises(CommandError, match="only when DEBUG is True"):
        call_command(command)
