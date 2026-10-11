from __future__ import annotations

import re
from dataclasses import dataclass

from ninja_bot.cs2.models import InvalidSteamIdError

# Longest accepted /link input; checked before any regex runs.
MAX_STEAM_INPUT_LENGTH = 200

STEAM_ID_ONLY_MESSAGE = (
    "That doesn't look like a Steam ID. "
    "Use a 17-digit SteamID64 or a `steamcommunity.com/profiles/<id>` link."
)

# re.ASCII: `\d` must not match other Unicode digits.
_STEAM_ID_RE = re.compile(r"7656119\d{10}", re.ASCII)
# Steam custom URL names: 2-32 ASCII letters, digits, `_` and `-`.
VANITY_NAME_RE = re.compile(r"[A-Za-z0-9_-]{2,32}", re.ASCII)
_PREFIX = r"(?:https?://)?(?:www\.)?steamcommunity\.com"
# Optional end of a profile URL: `/`, then an ignored `?...` query or `#...` fragment.
_URL_TAIL = r"/?(?:[?#].*)?"
_URL_FLAGS = re.ASCII | re.IGNORECASE | re.DOTALL
_PROFILE_URL_RE = re.compile(_PREFIX + r"/profiles/(\d+)" + _URL_TAIL, _URL_FLAGS)
_VANITY_URL_RE = re.compile(_PREFIX + r"/id/([^/?#]+)" + _URL_TAIL, _URL_FLAGS)
_STEAM_ID_LENGTH = 17


@dataclass(frozen=True)
class SteamId:
    value: str


@dataclass(frozen=True)
class VanityName:
    value: str


def is_vanity_name(name: str) -> bool:
    """True for a valid custom URL name; 17-digit numbers are reserved for SteamID64s."""
    if not VANITY_NAME_RE.fullmatch(name):
        return False
    return not (name.isdigit() and len(name) == _STEAM_ID_LENGTH)


def parse_steam_target(text: str) -> SteamId | VanityName:
    """Parse /link input: a SteamID64, a `/profiles/<id>` or `/id/<name>` URL, or a bare name."""
    if len(text) > MAX_STEAM_INPUT_LENGTH:
        raise InvalidSteamIdError()
    value = text.strip()
    if match := _PROFILE_URL_RE.fullmatch(value):
        if _STEAM_ID_RE.fullmatch(match.group(1)):
            return SteamId(match.group(1))
        raise InvalidSteamIdError()
    if match := _VANITY_URL_RE.fullmatch(value):
        value = match.group(1)
    elif _STEAM_ID_RE.fullmatch(value):
        return SteamId(value)
    if is_vanity_name(value):
        return VanityName(value)
    raise InvalidSteamIdError()


def parse_steam_id(text: str) -> str:
    """Return the SteamID64 from a bare ID or a `/profiles/<id>` URL; vanity names are rejected."""
    target = parse_steam_target(text)
    if isinstance(target, VanityName):
        raise InvalidSteamIdError(STEAM_ID_ONLY_MESSAGE)
    return target.value
