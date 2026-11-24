"""Offline and deferred invoice message senders."""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class MessageSendError(Exception):
    """Raised when an invoice message cannot be delivered."""


class MessageSender:
    """Interface implemented by invoice message senders."""

    def send(
        self,
        *,
        recipient_facebook_id: str,
        text: str,
        context: dict[str, Any],
    ) -> None:
        """Deliver an invoice message to one Facebook customer."""
        raise NotImplementedError


class ConsoleSender(MessageSender):
    """Log outgoing invoice messages without external delivery."""

    def send(
        self,
        *,
        recipient_facebook_id: str,
        text: str,
        context: dict[str, Any],
    ) -> None:
        """Write the message and routing context to the application log."""
        recipient_name = context.get("recipient_name", recipient_facebook_id)
        logger.info("-> Messenger to %s\n%s", recipient_name, text)


class GraphApiSender(MessageSender):
    """Deferred stub for future Facebook invoice messaging."""

    def send(
        self,
        *,
        recipient_facebook_id: str,
        text: str,
        context: dict[str, Any],
    ) -> None:
        """Raise until private-reply and Messenger policies are verified."""
        del recipient_facebook_id, text, context
        # TODO(verify): Decide private reply to the source comment vs Messenger send.
        # TODO(verify): Confirm required Page/App permissions and review scope.
        # TODO(verify): Confirm the applicable 24-hour messaging window.
        raise NotImplementedError("Graph API sending is deferred")


def get_message_sender() -> MessageSender:
    """Build the sender selected by MESSAGE_SENDER."""
    sender_name = settings.MESSAGE_SENDER.strip().lower()
    if sender_name == "console":
        return ConsoleSender()
    if sender_name == "graph_api":
        return GraphApiSender()
    raise ImproperlyConfigured(f"Unknown MESSAGE_SENDER: {sender_name}.")
