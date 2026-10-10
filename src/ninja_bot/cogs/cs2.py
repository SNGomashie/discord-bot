from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ninja_bot.cs2.client import LeetifyClient, StatsProvider
from ninja_bot.cs2.models import Cs2Error
from ninja_bot.cs2.steam import MAX_STEAM_INPUT_LENGTH, parse_steam_id
from ninja_bot.cs2.store import LinkStore, SqliteLinkStore
from ninja_bot.media.cooldown import UserCooldown

if TYPE_CHECKING:
    from ninja_bot.bot import NinjaBot

log = logging.getLogger(__name__)

GENERIC_ERROR_MESSAGE = "Something went wrong while fetching CS2 stats. Please try again later."
COOLDOWN_SECONDS = 10.0
UNLINKED_MESSAGE = "Your Steam account is no longer linked."
NOTHING_TO_UNLINK_MESSAGE = "You don't have a linked Steam account."


def linked_message(name: str) -> str:
    return f"Linked to **{discord.utils.escape_markdown(name)}** on Leetify."


class Cs2(commands.Cog):
    def __init__(
        self,
        stats: StatsProvider,
        links: LinkStore,
        cooldown: UserCooldown | None = None,
    ) -> None:
        self.stats = stats
        self.links = links
        # Used by the stats commands; taken after input is valid, refunded unless posted.
        self.cooldown = cooldown if cooldown is not None else UserCooldown(COOLDOWN_SECONDS)

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

    @app_commands.command(name="unlink", description="Remove your linked Steam account")
    async def unlink(self, interaction: discord.Interaction) -> None:
        await self.handle_unlink(interaction)

    async def handle_link(self, interaction: discord.Interaction, steam: str) -> None:
        try:
            steam64_id = parse_steam_id(steam)
            profile = await self.stats.get_profile(steam64_id)
            await self.links.set(interaction.user.id, steam64_id)
        except Cs2Error as exc:
            await interaction.response.send_message(exc.message, ephemeral=True)
            return
        except Exception:
            log.exception("Unexpected error linking a Steam account")
            await interaction.response.send_message(GENERIC_ERROR_MESSAGE, ephemeral=True)
            return
        name = profile.name or steam64_id
        await interaction.response.send_message(linked_message(name), ephemeral=True)

    async def handle_unlink(self, interaction: discord.Interaction) -> None:
        try:
            removed = await self.links.remove(interaction.user.id)
        except Exception:
            log.exception("Unexpected error unlinking a Steam account")
            await interaction.response.send_message(GENERIC_ERROR_MESSAGE, ephemeral=True)
            return
        message = UNLINKED_MESSAGE if removed else NOTHING_TO_UNLINK_MESSAGE
        await interaction.response.send_message(message, ephemeral=True)

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        log.error("App command error in Cs2 cog", exc_info=error)
        message = GENERIC_ERROR_MESSAGE
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: NinjaBot) -> None:
    client = LeetifyClient(bot.config.leetify_api_key)
    store = SqliteLinkStore(Path(bot.config.data_dir) / "cs2.sqlite3")
    await bot.add_cog(Cs2(client, store, UserCooldown(COOLDOWN_SECONDS)))
