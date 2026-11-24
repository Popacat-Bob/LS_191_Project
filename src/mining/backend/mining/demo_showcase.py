"""Shared file-based showcase state for the live-demo player and detector."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

SHOWCASE_STATE_PATH = Path(tempfile.gettempdir()) / "easylv_demo_showcase.json"
DEMO_NAME_PREFIX = "[DEMO] "

_BOLD = "\033[1m"
_BACKGROUND = "\033[46m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def write_showcase_state(state: dict[str, Any]) -> None:
    """Atomically publish showcase details without touching business tables."""
    SHOWCASE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=SHOWCASE_STATE_PATH.parent,
            prefix=f".{SHOWCASE_STATE_PATH.name}.",
            delete=False,
        ) as temporary_file:
            temporary_path = temporary_file.name
            json.dump(state, temporary_file)
        os.replace(temporary_path, SHOWCASE_STATE_PATH)
    finally:
        if temporary_path is not None and os.path.exists(temporary_path):
            os.unlink(temporary_path)


def clear_showcase_state() -> None:
    """Remove showcase state from a previous demo run, if present."""
    SHOWCASE_STATE_PATH.unlink(missing_ok=True)


def read_showcase_state() -> dict[str, Any] | None:
    """Read the current showcase state, returning None when no feed has run."""
    try:
        state = json.loads(SHOWCASE_STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if (
        not isinstance(state, dict)
        or not {
            "code",
            "name",
            "price",
            "comment_index",
            "comments_per_item",
        }
        <= state.keys()
    ):
        raise ValueError(f"Invalid showcase state in {SHOWCASE_STATE_PATH}.")
    return state


def format_showcase_banner(state: dict[str, Any], *, color: bool = True) -> str:
    """Format the shared item banner and current comment counter."""
    code = state["code"]
    banner = (
        f"===== [ CODE {code} is being showcased: {state['name']} - "
        f"PHP {state['price']} ] ====="
    )
    counter = (
        f"comment {state['comment_index']}/{state['comments_per_item']} for CODE {code}"
    )
    if color:
        banner = f"{_BOLD}{_BACKGROUND}{banner}{_RESET}"
        counter = f"{_DIM}{counter}{_RESET}"
    return f"\n{banner}\n{counter}\n"


def format_showcase_tag(code: str, *, color: bool = True) -> str:
    """Render the subdued current-code tag appended to player comments."""
    tag = f"[showcasing {code}]"
    return f"{_DIM}{tag}{_RESET}" if color else tag


def format_showcase_closing(
    code: str, count: int, expected_count: int, *, color: bool = True
) -> str:
    """Format the line that closes a completed or interrupted showcase."""
    if count == expected_count:
        text = (
            f"----- [ CODE {code} sold / showcase ended after {count} comments ] -----"
        )
    else:
        text = (
            f"----- [ CODE {code} showcase ended after {count} of "
            f"{expected_count} comments ] -----"
        )
    return f"{_BOLD}{text}{_RESET}" if color else text
