from __future__ import annotations

import re

from ninja_bot.cs2.models import InvalidSteamIdError

# Longest accepted /link input; checked before any regex runs.
MAX_STEAM_INPUT_LENGTH = 200

VANITY_URL_MESSAGE = (
    "Custom Steam URLs (`/id/<name>`) aren't supported. Open your Steam profile "
    "and copy the 17-digit number from the `steamcommunity.com/profiles/` URL."
)

# re.ASCII: `\d` must not match other Unicode digits.
_STEAM_ID_RE = re.compile(r"7656119\d{10}", re.ASCII)
_PREFIX = r"(?:https?://)?(?:www\.)?steamcommunity\.com"
_PROFILE_URL_RE = re.compile(_PREFIX + r"/profiles/(\d+)/?", re.ASCII | re.IGNORECASE)
_VANITY_URL_RE = re.compile(_PREFIX + r"/id/[^/]+/?", re.ASCII | re.IGNORECASE)


def parse_steam_id(text: str) -> str:
    """Return the SteamID64 from a bare ID or a `steamcommunity.com/profiles/<id>` URL."""
    if len(text) > MAX_STEAM_INPUT_LENGTH:
        raise InvalidSteamIdError()
    value = text.strip()
    if match := _PROFILE_URL_RE.fullmatch(value):
        value = match.group(1)
    elif _VANITY_URL_RE.fullmatch(value):
        raise InvalidSteamIdError(VANITY_URL_MESSAGE)
    if not _STEAM_ID_RE.fullmatch(value):
        raise InvalidSteamIdError()
    return value
