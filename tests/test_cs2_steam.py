import pytest

from ninja_bot.cs2.models import InvalidSteamIdError
from ninja_bot.cs2.steam import (
    MAX_STEAM_INPUT_LENGTH,
    STEAM_ID_ONLY_MESSAGE,
    SteamId,
    VanityName,
    is_vanity_name,
    parse_steam_id,
    parse_steam_target,
)

STEAM_ID = "76561197960287930"


@pytest.mark.parametrize(
    "text",
    [
        STEAM_ID,
        f"  {STEAM_ID}  ",
        f"https://steamcommunity.com/profiles/{STEAM_ID}",
        f"https://steamcommunity.com/profiles/{STEAM_ID}/",
        f"http://steamcommunity.com/profiles/{STEAM_ID}",
        f"https://www.steamcommunity.com/profiles/{STEAM_ID}/",
        f"www.steamcommunity.com/profiles/{STEAM_ID}",
        f"steamcommunity.com/profiles/{STEAM_ID}",
        f"steamcommunity.com/profiles/{STEAM_ID}/",
        f"HTTPS://SteamCommunity.com/profiles/{STEAM_ID}",
    ],
)
def test_accepts_id_and_profile_urls(text: str) -> None:
    assert parse_steam_target(text) == SteamId(STEAM_ID)
    assert parse_steam_id(text) == STEAM_ID


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("https://steamcommunity.com/id/gaben", "gaben"),
        ("https://steamcommunity.com/id/gaben/", "gaben"),
        ("http://steamcommunity.com/id/gaben", "gaben"),
        ("https://www.steamcommunity.com/id/gaben/", "gaben"),
        ("www.steamcommunity.com/id/gaben", "gaben"),
        ("steamcommunity.com/id/gaben", "gaben"),
        ("steamcommunity.com/id/gaben/", "gaben"),
        ("HTTPS://SteamCommunity.com/id/GabeN", "GabeN"),
        ("  https://steamcommunity.com/id/gaben  ", "gaben"),
        ("https://steamcommunity.com/id/gaben?l=english", "gaben"),
        ("https://steamcommunity.com/id/gaben/?xml=1", "gaben"),
        ("https://steamcommunity.com/id/gaben#top", "gaben"),
        ("https://steamcommunity.com/id/gaben/#top", "gaben"),
        ("https://steamcommunity.com/id/the_real-gabe_1", "the_real-gabe_1"),
        ("https://steamcommunity.com/id/1234", "1234"),
    ],
)
def test_vanity_urls_return_the_name(text: str, name: str) -> None:
    assert parse_steam_target(text) == VanityName(name)


@pytest.mark.parametrize(
    "text",
    [
        "gaben",
        "GabeN",
        "  gaben  ",
        "ab",  # lower bound
        "a" * 32,  # upper bound
        "the_real-gabe_1",
        "--",
        "1234",
        "1" * 16,
        "1" * 18,
        "12345678901234567890",
        "-76561197960287930",  # allowed characters, not 17 digits
    ],
)
def test_bare_vanity_names(text: str) -> None:
    assert parse_steam_target(text) == VanityName(text.strip())


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "a",  # below lower bound
        "a" * 33,  # above upper bound
        "gabe newell",
        "gabe/newell",
        "gabe.newell",
        "gabe@newell",
        "gäben",
        "ｇａｂｅｎ",  # fullwidth letters
        "١٢٣٤",  # Arabic-Indic digits
        "७६५६११९७९६०२८७९३०",  # Devanagari digits
        "７６５６１１９７９６０２８７９３０",  # fullwidth digits
        "12345678901234567",  # 17 digits, wrong prefix: not an ID and not a name
        "76561197960287930.0",
        f"https://example.com/profiles/{STEAM_ID}",
        f"https://steamcommunity.com.evil.com/profiles/{STEAM_ID}",
        f"https://evilsteamcommunity.com/profiles/{STEAM_ID}",
        f"ftp://steamcommunity.com/profiles/{STEAM_ID}",
        f"https://steamcommunity.com/profiles/{STEAM_ID}/games",
        f"https://steamcommunity.com/profiles/{STEAM_ID}?tab=all",
        "https://steamcommunity.com/profiles/",
        "https://steamcommunity.com/profiles/12345678901234567",
        "https://steamcommunity.com/profiles/gaben",
        "https://evil.example/id/gaben",
        "evil.example/id/gaben",
        "https://steamcommunity.com.evil.example/id/gaben",
        "steamcommunity.com.evil.example/id/gaben",
        "https://steamcommunity.com/id/",
        "https://steamcommunity.com/id/g",
        "https://steamcommunity.com/id/gaben/games",
        "https://steamcommunity.com/id/gabe%20newell",
        "https://steamcommunity.com/id/gäben",
        "https://steamcommunity.com/id/12345678901234567",
        f"{STEAM_ID} {STEAM_ID}",
    ],
)
def test_rejects_invalid_input(text: str) -> None:
    with pytest.raises(InvalidSteamIdError) as exc_info:
        parse_steam_target(text)
    assert exc_info.value.message == InvalidSteamIdError.default_message


def test_default_message_lists_accepted_forms() -> None:
    message = InvalidSteamIdError.default_message
    for form in ("17-digit SteamID64", "/profiles/<id>", "/id/<name>", "custom URL name"):
        assert form in message


def test_rejects_overlong_input_before_parsing() -> None:
    text = STEAM_ID + " " * (MAX_STEAM_INPUT_LENGTH - len(STEAM_ID) + 1)
    with pytest.raises(InvalidSteamIdError):
        parse_steam_target(text)
    assert parse_steam_target(text[:-1]) == SteamId(STEAM_ID)


def test_rejects_overlong_vanity_url() -> None:
    text = "https://steamcommunity.com/id/gaben?" + "a" * MAX_STEAM_INPUT_LENGTH
    with pytest.raises(InvalidSteamIdError) as exc_info:
        parse_steam_target(text)
    assert exc_info.value.message == InvalidSteamIdError.default_message


@pytest.mark.parametrize("name", ["gaben", "1234", "a" * 32])
def test_is_vanity_name_accepts_names(name: str) -> None:
    assert is_vanity_name(name)


@pytest.mark.parametrize("name", ["", "a", "a" * 33, "gä", "a b", STEAM_ID, "1" * 17])
def test_is_vanity_name_rejects_others(name: str) -> None:
    assert not is_vanity_name(name)


@pytest.mark.parametrize("text", ["gaben", "https://steamcommunity.com/id/gaben"])
def test_parse_steam_id_rejects_vanity_input(text: str) -> None:
    with pytest.raises(InvalidSteamIdError) as exc_info:
        parse_steam_id(text)
    assert exc_info.value.message == STEAM_ID_ONLY_MESSAGE
    assert "17-digit" in STEAM_ID_ONLY_MESSAGE
