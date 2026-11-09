"""Randomised canned lines without an LLM (no discord import)."""

from __future__ import annotations

import random
from collections.abc import Sequence


class LinePicker:
    """Picks lines at random, never the same line twice in a row from one pool."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng if rng is not None else random.Random()
        self._last: dict[int, int] = {}

    def pick(self, pool: Sequence[str], **fields: object) -> str:
        """A random line from `pool` with `{placeholders}` filled from `fields`."""
        if not pool:
            raise ValueError("pool must not be empty")
        key = id(pool)
        indexes = [i for i in range(len(pool)) if i != self._last.get(key)]
        index = self._rng.choice(indexes) if indexes else 0
        self._last[key] = index
        return pool[index].format(**fields)
