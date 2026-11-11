"""Schema fragments shared by the hand-written tool modules."""

from __future__ import annotations

from typing import Any

__all__ = ["HANDLED"]

HANDLED: dict[str, Any] = {
    "type": "array",
    "items": {"type": "integer"},
    "maxItems": 50,
    "description": (
        "Ids (the id attribute of <session-message>) of messages from other sessions this call "
        "answers or that need no answer; unmarked ones carry over to the next leader."
    ),
}
