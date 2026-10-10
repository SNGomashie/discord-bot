import pytest

from ninja_bot.cs2.models import InvalidSteamIdError
from ninja_bot.cs2.steam import MAX_STEAM_INPUT_LENGTH, VANITY_URL_MESSAGE, parse_steam_id

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
    assert parse_steam_id(text) == STEAM_ID


@pytest.mark.parametrize(
    "text",
    [
        "https://steamcommunity.com/id/gaben",
        "https://steamcommunity.com/id/gaben/",
        "steamcommunity.com/id/gaben",
        "www.steamcommunity.com/id/76561197960287930",
    ],
)
def test_vanity_urls_explain_how_to_find_the_id(text: str) -> None:
    with pytest.raises(InvalidSteamIdError) as exc_info:
        parse_steam_id(text)
    assert exc_info.value.message == VANITY_URL_MESSAGE
    assert "17-digit" in VANITY_URL_MESSAGE
    assert "/profiles/" in VANITY_URL_MESSAGE


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "gaben",
        "7656119796028793a",
        "7656119796028793",  # 16 digits
        "765611979602879300",  # 18 digits
        "12345678901234567",  # wrong prefix
        "-76561197960287930",
        "76561197960287930.0",
        "７６５６１１９７９６０２８７９３０",  # fullwidth digits
        "७६५६११९७९६०२८७९३०",  # Devanagari digits
        f"https://example.com/profiles/{STEAM_ID}",
        f"https://steamcommunity.com.evil.com/profiles/{STEAM_ID}",
        f"https://evilsteamcommunity.com/profiles/{STEAM_ID}",
        f"ftp://steamcommunity.com/profiles/{STEAM_ID}",
        f"https://steamcommunity.com/profiles/{STEAM_ID}/games",
        f"https://steamcommunity.com/profiles/{STEAM_ID}?tab=all",
        "https://steamcommunity.com/profiles/",
        "https://steamcommunity.com/profiles/12345678901234567",
        "https://steamcommunity.com/profiles/gaben",
        f"{STEAM_ID} {STEAM_ID}",
    ],
)
def test_rejects_invalid_input(text: str) -> None:
    with pytest.raises(InvalidSteamIdError) as exc_info:
        parse_steam_id(text)
    assert exc_info.value.message == InvalidSteamIdError.default_message


def test_rejects_overlong_input_before_parsing() -> None:
    text = STEAM_ID + " " * (MAX_STEAM_INPUT_LENGTH - len(STEAM_ID) + 1)
    with pytest.raises(InvalidSteamIdError):
        parse_steam_id(text)
    assert parse_steam_id(text[:-1]) == STEAM_ID


def test_rejects_overlong_vanity_url_with_generic_message() -> None:
    text = "https://steamcommunity.com/id/" + "a" * MAX_STEAM_INPUT_LENGTH
    with pytest.raises(InvalidSteamIdError) as exc_info:
        parse_steam_id(text)
    assert exc_info.value.message == InvalidSteamIdError.default_message
