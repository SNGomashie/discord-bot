"""Per-user cooldown that is only taken once a request is known to be valid."""

from __future__ import annotations

import time
from collections.abc import Callable


class UserCooldown:
    """Allows one use per `per_seconds` per user; failed requests can be refunded."""

    def __init__(self, per_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_seconds = per_seconds
        self._clock = clock
        self._last_use: dict[int, float] = {}

    def try_acquire(self, user_id: int) -> float:
        """Return 0.0 and record the use if allowed, else the remaining seconds."""
        now = self._clock()
        last = self._last_use.get(user_id)
        if last is not None:
            remaining = self.per_seconds - (now - last)
            if remaining > 0:
                return remaining
        self._last_use[user_id] = now
        return 0.0

    def refund(self, user_id: int) -> None:
        """Forget the user's last use so they can retry immediately."""
        self._last_use.pop(user_id, None)
