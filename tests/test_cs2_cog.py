from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import app_commands

from ninja_bot.bot import EXTENSIONS
from ninja_bot.cogs import cs2 as cs2_cog
from ninja_bot.cogs.cs2 import (
    COOLDOWN_SECONDS,
    GENERIC_ERROR_MESSAGE,
    NOTHING_TO_UNLINK_MESSAGE,
    UNLINKED_MESSAGE,
    Cs2,
    linked_message,
)
from ninja_bot.config import load_config
from ninja_bot.cs2.client import LeetifyClient
from ninja_bot.cs2.models import (
    ApiError,
    Cs2Error,
    InvalidSteamIdError,
    Match,
    PlayerNotFoundError,
    PrivateProfileError,
    Profile,
)
from ninja_bot.cs2.steam import MAX_STEAM_INPUT_LENGTH, VANITY_URL_MESSAGE
from ninja_bot.cs2.store import SqliteLinkStore
from ninja_bot.media.cooldown import UserCooldown

STEAM_ID = "76561198000000001"
USER_ID = 42


def make_profile(steam64_id: str = STEAM_ID, name: str | None = "Ninja") -> Profile:
    return Profile(
        steam64_id=steam64_id,
        name=name,
        winrate=None,
        total_matches=None,
        aim=None,
        positioning=None,
        utility=None,
        clutch=None,
        opening=None,
        ranks=None,
        recent_matches=(),
    )


class FakeStats:
    def __init__(self, profile: Profile | None = None, error: Exception | None = None) -> None:
        self.profile = profile or make_profile()
        self.error = error
        self.profile_calls: list[str] = []

    async def get_profile(self, steam64_id: str) -> Profile:
        self.profile_calls.append(steam64_id)
        if self.error is not None:
            raise self.error
        return self.profile

    async def get_recent_matches(self, steam64_id: str) -> list[Match]:
        raise NotImplementedError

    async def get_match(self, game_id: str) -> Match:
        raise NotImplementedError


class MemoryLinkStore:
    def __init__(self, links: dict[int, str] | None = None) -> None:
        self.links = dict(links or {})

    async def get(self, discord_id: int) -> str | None:
        return self.links.get(discord_id)

    async def set(self, discord_id: int, steam64_id: str) -> None:
        self.links[discord_id] = steam64_id

    async def remove(self, discord_id: int) -> bool:
        return self.links.pop(discord_id, None) is not None

    async def all_links(self) -> dict[int, str]:
        return dict(self.links)


def make_interaction(user_id: int = USER_ID) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def reply(interaction: MagicMock) -> tuple[tuple[Any, ...], dict[str, Any]]:
    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    return args, kwargs


def make_cog(
    stats: FakeStats | None = None, store: MemoryLinkStore | None = None
) -> tuple[Cs2, FakeStats, MemoryLinkStore]:
    stats = stats or FakeStats()
    store = store if store is not None else MemoryLinkStore()
    return Cs2(stats, store, UserCooldown(COOLDOWN_SECONDS)), stats, store


async def test_link_success_stores_and_replies_ephemeral() -> None:
    cog, stats, store = make_cog()
    interaction = make_interaction()
    await Cs2.link.callback(cog, interaction, STEAM_ID)
    assert stats.profile_calls == [STEAM_ID]
    assert store.links == {USER_ID: STEAM_ID}
    args, kwargs = reply(interaction)
    assert args == (linked_message("Ninja"),)
    assert kwargs == {"ephemeral": True}
    assert "Ninja" in args[0]


async def test_link_accepts_profile_url() -> None:
    cog, _, store = make_cog()
    await cog.handle_link(make_interaction(), f"https://steamcommunity.com/profiles/{STEAM_ID}/")
    assert store.links == {USER_ID: STEAM_ID}


async def test_link_replaces_existing_link() -> None:
    cog, _, store = make_cog(store=MemoryLinkStore({USER_ID: "76561198000000999"}))
    await cog.handle_link(make_interaction(), STEAM_ID)
    assert store.links == {USER_ID: STEAM_ID}


async def test_link_without_name_falls_back_to_steam_id() -> None:
    cog, _, _ = make_cog(FakeStats(make_profile(name=None)))
    interaction = make_interaction()
    await cog.handle_link(interaction, STEAM_ID)
    args, _ = reply(interaction)
    assert STEAM_ID in args[0]


def test_linked_message_escapes_markdown() -> None:
    assert "\\*" in linked_message("a*b")


@pytest.mark.parametrize(
    ("steam", "message"),
    [
        ("not an id", InvalidSteamIdError.default_message),
        ("https://steamcommunity.com/id/someone", VANITY_URL_MESSAGE),
    ],
)
async def test_link_invalid_id_is_ephemeral_and_skips_api(steam: str, message: str) -> None:
    cog, stats, store = make_cog()
    interaction = make_interaction()
    await cog.handle_link(interaction, steam)
    assert stats.profile_calls == []
    assert store.links == {}
    assert reply(interaction) == ((message,), {"ephemeral": True})


@pytest.mark.parametrize(
    "error", [PlayerNotFoundError(), PrivateProfileError(), ApiError()], ids=type
)
async def test_link_api_errors_are_ephemeral_and_not_stored(error: Cs2Error) -> None:
    cog, _, store = make_cog(FakeStats(error=error))
    interaction = make_interaction()
    await cog.handle_link(interaction, STEAM_ID)
    assert store.links == {}
    assert reply(interaction) == ((error.message,), {"ephemeral": True})


async def test_link_unexpected_error_is_generic_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cog, _, store = make_cog(FakeStats(error=RuntimeError("boom")))
    interaction = make_interaction()
    await cog.handle_link(interaction, STEAM_ID)
    assert store.links == {}
    assert reply(interaction) == ((GENERIC_ERROR_MESSAGE,), {"ephemeral": True})
    assert "Unexpected error linking" in caplog.text


async def test_unlink_removes_existing_link() -> None:
    cog, _, store = make_cog(store=MemoryLinkStore({USER_ID: STEAM_ID, 7: STEAM_ID}))
    interaction = make_interaction()
    await Cs2.unlink.callback(cog, interaction)
    assert store.links == {7: STEAM_ID}
    assert reply(interaction) == ((UNLINKED_MESSAGE,), {"ephemeral": True})


async def test_unlink_without_link() -> None:
    cog, _, _ = make_cog()
    interaction = make_interaction()
    await cog.handle_unlink(interaction)
    assert reply(interaction) == ((NOTHING_TO_UNLINK_MESSAGE,), {"ephemeral": True})


async def test_unlink_unexpected_error_is_generic(caplog: pytest.LogCaptureFixture) -> None:
    store = MemoryLinkStore()
    store.remove = AsyncMock(side_effect=RuntimeError("disk"))  # type: ignore[method-assign]
    cog, _, _ = make_cog(store=store)
    interaction = make_interaction()
    await cog.handle_unlink(interaction)
    assert reply(interaction) == ((GENERIC_ERROR_MESSAGE,), {"ephemeral": True})
    assert "Unexpected error unlinking" in caplog.text


def test_link_steam_option_metadata() -> None:
    param = Cs2.link._params["steam"]
    assert param.required
    assert str(param.description)
    assert param.to_dict()["max_length"] == MAX_STEAM_INPUT_LENGTH == 200


def test_unlink_has_no_options() -> None:
    assert Cs2.unlink._params == {}


@pytest.mark.parametrize("done", [False, True])
async def test_cog_app_command_error_is_generic(
    done: bool, caplog: pytest.LogCaptureFixture
) -> None:
    cog, _, _ = make_cog()
    interaction = make_interaction()
    interaction.response.is_done = MagicMock(return_value=done)
    await cog.cog_app_command_error(interaction, app_commands.AppCommandError("x"))
    send = interaction.followup.send if done else interaction.response.send_message
    send.assert_awaited_once_with(GENERIC_ERROR_MESSAGE, ephemeral=True)
    assert "App command error in Cs2 cog" in caplog.text


def test_extension_registered() -> None:
    assert "ninja_bot.cogs.cs2" in EXTENSIONS


async def test_setup_registers_cog(tmp_path: Path) -> None:
    bot = MagicMock()
    bot.config = load_config(
        {"DISCORD_TOKEN": "x", "GUILD_ID": "1", "LEETIFY_API_KEY": "key", "DATA_DIR": str(tmp_path)}
    )
    bot.add_cog = AsyncMock()
    await cs2_cog.setup(bot)
    bot.add_cog.assert_awaited_once()
    cog = bot.add_cog.call_args.args[0]
    assert isinstance(cog, Cs2)
    assert isinstance(cog.stats, LeetifyClient)
    assert isinstance(cog.links, SqliteLinkStore)
    assert cog.links._path == tmp_path / "cs2.sqlite3"
    assert cog.cooldown.per_seconds == COOLDOWN_SECONDS == 10.0


async def test_cog_unload_closes_client() -> None:
    client = LeetifyClient()
    client.aclose = AsyncMock()  # type: ignore[method-assign]
    cog = Cs2(client, MemoryLinkStore())
    await cog.cog_unload()
    client.aclose.assert_awaited_once()


async def test_cog_unload_with_fake_provider_is_noop() -> None:
    cog, _, _ = make_cog()
    await cog.cog_unload()
