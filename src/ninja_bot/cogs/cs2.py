from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ninja_bot.cs2.awards import compute_awards
from ninja_bot.cs2.client import LeetifyClient, StatsProvider
from ninja_bot.cs2.embed import build_match_embed, build_profile_embed
from ninja_bot.cs2.models import Cs2Error, Match, NoMatchesError, NotLinkedError, Profile
from ninja_bot.cs2.steam import MAX_STEAM_INPUT_LENGTH, parse_steam_id
from ninja_bot.cs2.store import LinkStore, SqliteLinkStore
from ninja_bot.media.cooldown import UserCooldown

if TYPE_CHECKING:
    from ninja_bot.bot import NinjaBot

log = logging.getLogger(__name__)

GENERIC_ERROR_MESSAGE = "Something went wrong while fetching CS2 stats. Please try again later."
COOLDOWN_SECONDS = 10.0
LINK_COOLDOWN_SECONDS = 15.0
ALREADY_LINKED_MESSAGE = "You're already linked to that Steam account."
UNLINKED_MESSAGE = "Your Steam account is no longer linked."
NOTHING_TO_UNLINK_MESSAGE = "You don't have a linked Steam account."
MAX_MATCH_ID_LENGTH = 64
INVALID_MATCH_ID_MESSAGE = "That doesn't look like a Leetify match id."


def linked_message(name: str) -> str:
    return f"Linked to **{discord.utils.escape_markdown(name)}** on Leetify."


def not_linked_message(name: str) -> str:
    """NotLinkedError text for someone other than the caller."""
    return (
        f"**{discord.utils.escape_markdown(name)}** hasn't linked a Steam account yet. "
        "They can run `/link` to do it."
    )


def cooldown_message(retry_after: float) -> str:
    """User-facing reply for a CS2 stats cooldown, rounded up to whole seconds."""
    seconds = max(1, math.ceil(retry_after))
    unit = "second" if seconds == 1 else "seconds"
    return f"Slow down! You can use CS2 stats commands again in {seconds} {unit}."


def clean_match_id(match: str | None) -> str | None:
    """Strip an optional match id; blank means "latest". Raises Cs2Error if too long."""
    if match is None:
        return None
    if len(match) > MAX_MATCH_ID_LENGTH:
        raise Cs2Error(INVALID_MATCH_ID_MESSAGE)
    return match.strip() or None


async def _send_safely(send: Awaitable[object]) -> bool:
    """Await a Discord send; log and swallow HTTP errors (e.g. an expired token)."""
    try:
        await send
    except discord.HTTPException:
        log.warning("Could not send CS2 response", exc_info=True)
        return False
    return True


class Cs2(commands.Cog):
    def __init__(
        self,
        stats: StatsProvider,
        links: LinkStore,
        cooldown: UserCooldown | None = None,
        link_cooldown: UserCooldown | None = None,
    ) -> None:
        self.stats = stats
        self.links = links
        # Used by the stats commands; taken after input is valid, refunded unless posted.
        self.cooldown = cooldown if cooldown is not None else UserCooldown(COOLDOWN_SECONDS)
        # Separate from the stats cooldown; never refunded once Leetify was called.
        self.link_cooldown = (
            link_cooldown if link_cooldown is not None else UserCooldown(LINK_COOLDOWN_SECONDS)
        )

    async def cog_unload(self) -> None:
        if isinstance(self.stats, LeetifyClient):
            await self.stats.aclose()

    @app_commands.command(name="link", description="Link your Steam account for CS2 stats")
    @app_commands.describe(
        steam="Your SteamID64 or steamcommunity.com/profiles/<id> link",
    )
    async def link(
        self,
        interaction: discord.Interaction,
        steam: app_commands.Range[str, 1, MAX_STEAM_INPUT_LENGTH],
    ) -> None:
        await self.handle_link(interaction, steam)

    @app_commands.command(name="scoreboard", description="Show a CS2 match scoreboard")
    @app_commands.describe(
        player="Whose match to show (defaults to you)",
        match="A Leetify match id (defaults to the latest match)",
    )
    async def scoreboard(
        self,
        interaction: discord.Interaction,
        player: discord.Member | None = None,
        match: app_commands.Range[str, 1, MAX_MATCH_ID_LENGTH] | None = None,
    ) -> None:
        await self.handle_scoreboard(interaction, player, match)

    @app_commands.command(name="profile", description="Show a CS2 player's Leetify profile")
    @app_commands.describe(player="Whose profile to show (defaults to you)")
    async def profile(
        self, interaction: discord.Interaction, player: discord.Member | None = None
    ) -> None:
        await self.handle_profile(interaction, player)

    @app_commands.command(name="unlink", description="Remove your linked Steam account")
    async def unlink(self, interaction: discord.Interaction) -> None:
        await self.handle_unlink(interaction)

    async def handle_link(self, interaction: discord.Interaction, steam: str) -> None:
        try:
            steam64_id = parse_steam_id(steam)
        except Cs2Error as exc:
            await interaction.response.send_message(exc.message, ephemeral=True)
            return

        try:
            current = await self.links.get(interaction.user.id)
        except Exception as exc:
            await self._reply_lookup_error(interaction, exc)
            return
        if current == steam64_id:
            await interaction.response.send_message(ALREADY_LINKED_MESSAGE, ephemeral=True)
            return

        retry_after = self.link_cooldown.try_acquire(interaction.user.id)
        if retry_after > 0:
            await interaction.response.send_message(cooldown_message(retry_after), ephemeral=True)
            return

        # Leetify can be slow; defer so Discord doesn't time out the interaction.
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            profile = await self.stats.get_profile(steam64_id)
            await self.links.set(interaction.user.id, steam64_id)
        except Cs2Error as exc:
            await _send_safely(interaction.followup.send(exc.message, ephemeral=True))
            return
        except Exception:
            log.exception("Unexpected error linking a Steam account")
            await _send_safely(interaction.followup.send(GENERIC_ERROR_MESSAGE, ephemeral=True))
            return
        name = profile.name or steam64_id
        await _send_safely(interaction.followup.send(linked_message(name), ephemeral=True))

    async def handle_unlink(self, interaction: discord.Interaction) -> None:
        try:
            removed = await self.links.remove(interaction.user.id)
        except Exception:
            log.exception("Unexpected error unlinking a Steam account")
            await interaction.response.send_message(GENERIC_ERROR_MESSAGE, ephemeral=True)
            return
        message = UNLINKED_MESSAGE if removed else NOTHING_TO_UNLINK_MESSAGE
        await interaction.response.send_message(message, ephemeral=True)

    async def handle_scoreboard(
        self,
        interaction: discord.Interaction,
        player: discord.Member | None = None,
        match: str | None = None,
    ) -> None:
        try:
            game_id = clean_match_id(match)
            target = player if player is not None else interaction.user
            steam64_id = await self._linked_steam_id(interaction, target)
        except Exception as exc:
            await self._reply_lookup_error(interaction, exc)
            return
        await self._run_with_cooldown(
            interaction, lambda: self._scoreboard_deferred(interaction, steam64_id, game_id)
        )

    async def handle_profile(
        self, interaction: discord.Interaction, player: discord.Member | None = None
    ) -> None:
        try:
            target = player if player is not None else interaction.user
            steam64_id = await self._linked_steam_id(interaction, target)
        except Exception as exc:
            await self._reply_lookup_error(interaction, exc)
            return
        await self._run_with_cooldown(
            interaction, lambda: self._profile_deferred(interaction, steam64_id)
        )

    async def _reply_lookup_error(self, interaction: discord.Interaction, exc: Exception) -> None:
        """Ephemeral reply for a failed input check or link lookup (before the defer)."""
        if isinstance(exc, Cs2Error):
            await interaction.response.send_message(exc.message, ephemeral=True)
            return
        log.error("Unexpected error looking up a CS2 link", exc_info=exc)
        await interaction.response.send_message(GENERIC_ERROR_MESSAGE, ephemeral=True)

    async def _run_with_cooldown(
        self, interaction: discord.Interaction, work: Callable[[], Awaitable[bool]]
    ) -> None:
        """Take the caller's cooldown, defer and run `work`; refund unless it posted."""
        user_id = interaction.user.id
        retry_after = self.cooldown.try_acquire(user_id)
        if retry_after > 0:
            await interaction.response.send_message(cooldown_message(retry_after), ephemeral=True)
            return

        posted = False
        try:
            await interaction.response.defer(thinking=True)
            posted = await work()
        finally:
            if not posted:
                self.cooldown.refund(user_id)

    async def _linked_steam_id(
        self, interaction: discord.Interaction, target: discord.User | discord.Member
    ) -> str:
        steam64_id = await self.links.get(target.id)
        if steam64_id is None:
            if target.id == interaction.user.id:
                raise NotLinkedError()
            raise NotLinkedError(not_linked_message(target.display_name))
        return steam64_id

    async def _scoreboard_deferred(
        self, interaction: discord.Interaction, steam64_id: str, game_id: str | None
    ) -> bool:
        """Fetch and post the scoreboard; True only if the embed was posted."""
        try:
            match = await self._fetch_match(steam64_id, game_id)
            profile = await self._fetch_profile_or_none(steam64_id)
            embed = build_match_embed(match, compute_awards(match), steam64_id, profile)
        except Cs2Error as exc:
            await _send_safely(interaction.followup.send(exc.message))
            return False
        except Exception:
            log.exception("Unexpected error building a CS2 scoreboard")
            await _send_safely(interaction.followup.send(GENERIC_ERROR_MESSAGE))
            return False
        return await _send_safely(interaction.followup.send(embed=embed))

    async def _profile_deferred(self, interaction: discord.Interaction, steam64_id: str) -> bool:
        """Fetch and post the profile; True only if the embed was posted."""
        try:
            profile = await self.stats.get_profile(steam64_id)
            embed = build_profile_embed(profile)
        except Cs2Error as exc:
            await _send_safely(interaction.followup.send(exc.message))
            return False
        except Exception:
            log.exception("Unexpected error building a CS2 profile")
            await _send_safely(interaction.followup.send(GENERIC_ERROR_MESSAGE))
            return False
        return await _send_safely(interaction.followup.send(embed=embed))

    async def _fetch_match(self, steam64_id: str, game_id: str | None) -> Match:
        if game_id is not None:
            return await self.stats.get_match(game_id)
        matches = await self.stats.get_recent_matches(steam64_id)
        if not matches:
            raise NoMatchesError()
        return matches[0]

    async def _fetch_profile_or_none(self, steam64_id: str) -> Profile | None:
        """The profile only adds the breakdown, so its CS2 errors don't stop the scoreboard."""
        try:
            return await self.stats.get_profile(steam64_id)
        except Cs2Error as exc:
            log.info("Posting scoreboard without profile: %s", type(exc).__name__)
            return None

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        log.error("App command error in Cs2 cog", exc_info=error)
        message = GENERIC_ERROR_MESSAGE
        if interaction.response.is_done():
            await _send_safely(interaction.followup.send(message, ephemeral=True))
        else:
            await _send_safely(interaction.response.send_message(message, ephemeral=True))


async def setup(bot: NinjaBot) -> None:
    client = LeetifyClient(bot.config.leetify_api_key)
    store = SqliteLinkStore(Path(bot.config.data_dir) / "cs2.sqlite3")
    await bot.add_cog(
        Cs2(client, store, UserCooldown(COOLDOWN_SECONDS), UserCooldown(LINK_COOLDOWN_SECONDS))
    )
