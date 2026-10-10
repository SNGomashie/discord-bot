from __future__ import annotations

from urllib.parse import urlsplit

from ninja_bot.media.models import InvalidUrlError


def validate_url(text: str) -> str:
    """Return the stripped URL if it is http(s) with a host, else raise InvalidUrlError."""
    url = text.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        raise InvalidUrlError() from None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise InvalidUrlError()
    return url
