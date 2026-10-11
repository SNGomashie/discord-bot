"""Forgiving command-name lookup, suggestions and autocomplete for /help.

Pure functions over plain name lists; the cog passes the registered command names.
"""

from __future__ import annotations

from collections.abc import Sequence

AUTOCOMPLETE_LIMIT = 25  # Discord's maximum number of autocomplete choices


def normalize_name(text: str) -> str:
    """Strip whitespace and one leading `/`, then lower-case."""
    text = text.strip()
    if text.startswith("/"):
        text = text[1:]
    return text.lower()


def find_command(text: str, names: Sequence[str]) -> str | None:
    """Return the name in `names` matching `text` after normalising both, else None."""
    wanted = normalize_name(text)
    if not wanted:
        return None
    for name in names:
        if normalize_name(name) == wanted:
            return name
    return None


def _ranked(text: str, names: Sequence[str]) -> list[str]:
    """Unique names containing `text`: prefix matches first, then the rest, each alphabetical.

    An empty (normalised) text matches every name.
    """
    wanted = normalize_name(text)
    unique = sorted(set(names), key=lambda name: (normalize_name(name), name))
    prefix = [name for name in unique if normalize_name(name).startswith(wanted)]
    rest = [name for name in unique if name not in prefix and wanted in normalize_name(name)]
    return prefix + rest


def suggest(text: str, names: Sequence[str], limit: int = 3) -> list[str]:
    """Up to `limit` names that start with or contain `text` (prefix matches first)."""
    return _ranked(text, names)[: max(limit, 0)]


def autocomplete_names(
    current: str, names: Sequence[str], limit: int = AUTOCOMPLETE_LIMIT
) -> list[str]:
    """Names containing `current` for Discord autocomplete, at most `limit` (max 25)."""
    limit = min(limit, AUTOCOMPLETE_LIMIT)
    return _ranked(current, names)[: max(limit, 0)]
