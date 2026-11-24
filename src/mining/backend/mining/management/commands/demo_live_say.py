"""Type comments into the demo live chat (see demo_live_chat)."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from mining import demo_channel
from mining.demo_channel import CHAT_INBOX, YOU_PREFIX
from mining.demo_showcase import read_showcase_state
from mining.management.commands.demo_live_chat import comment_payload


class Command(BaseCommand):
    help = "Prompt for comments and send them to the demo live chat."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--name", default="You", help="Your commenter name.")

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            raise CommandError("demo_live_say is available only when DEBUG is True.")
        name = options["name"].strip()
        if not name:
            raise CommandError("--name must not be blank.")

        self.stdout.write("Type a comment and press Enter; the prompt shows the code.")
        try:
            while True:
                showcase = read_showcase_state()
                hint = f"mine {showcase['code']}" if showcase else "mine <code>"
                message = input(f"[{hint}] > ").strip()
                if message:
                    demo_channel.push(
                        CHAT_INBOX,
                        comment_payload(
                            name=name,
                            commenter_id=f"{YOU_PREFIX}{name.casefold()}",
                            message=message,
                        ),
                    )
        except (EOFError, KeyboardInterrupt):
            self.stdout.write("")
