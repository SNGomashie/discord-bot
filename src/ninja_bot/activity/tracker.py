from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Open:
    """Start a session in `channel`."""

    channel: int


@dataclass(frozen=True)
class Close:
    """End the member's open session."""


@dataclass(frozen=True)
class Move:
    """End the open session and start a new one in `channel`."""

    channel: int


@dataclass(frozen=True)
class Nothing:
    """No change to the member's sessions."""


Action = Open | Close | Move | Nothing


def plan_transition(
    before_channel: int | None, after_channel: int | None, afk_channel: int | None
) -> Action:
    """What a voice state change means for tracking; the AFK channel counts as not in voice."""
    before = None if before_channel == afk_channel else before_channel
    after = None if after_channel == afk_channel else after_channel
    if before == after:
        return Nothing()
    if before is None:
        assert after is not None
        return Open(after)
    if after is None:
        return Close()
    return Move(after)
