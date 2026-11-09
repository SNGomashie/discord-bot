"""Pure clanker logic: detecting the word and rate-limiting replies per channel."""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from collections.abc import Callable

CLANKER_COOLDOWN_SECONDS = 60
MAX_TRACKED_CHANNELS = 1000
# People who answer one of the bot's clanker replies are timed out this long.
TIMEOUT_SECONDS = 60
MAX_TRACKED_REPLIES = 500

_CLANKER = re.compile(r"\bclankers?\b", re.IGNORECASE)


def mentions_clanker(content: str) -> bool:
    """True when the text contains the word `clanker` / `clankers` (any case)."""
    return _CLANKER.search(content) is not None


class ClankerCooldown:
    """At most one reply per channel per `seconds`; `allow` records the reply."""

    def __init__(
        self,
        seconds: float = CLANKER_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.seconds = seconds
        self.clock = clock
        self._last: dict[int, float] = {}

    def allow(self, channel_id: int) -> bool:
        now = self.clock()
        last = self._last.get(channel_id)
        if last is not None and now - last < self.seconds:
            return False
        if len(self._last) >= MAX_TRACKED_CHANNELS:
            self._last = {c: t for c, t in self._last.items() if now - t < self.seconds}
            while len(self._last) >= MAX_TRACKED_CHANNELS:
                del self._last[next(iter(self._last))]  # oldest first (insertion order)
        self._last[channel_id] = now
        return True


class ReplyTracker:
    """Remembers the ids of the bot's recent clanker replies (bounded, oldest dropped)."""

    def __init__(self, limit: int = MAX_TRACKED_REPLIES) -> None:
        self.limit = limit
        self._ids: OrderedDict[int, None] = OrderedDict()

    def remember(self, message_id: int) -> None:
        self._ids[message_id] = None
        while len(self._ids) > self.limit:
            self._ids.popitem(last=False)

    def is_reply_to_ours(self, message_id: int | None) -> bool:
        return message_id is not None and message_id in self._ids
