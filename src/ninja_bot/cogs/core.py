from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


def format_latency(seconds: float) -> str:
    return f"Pong! {round(seconds * 1000)} ms"


class Core(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="Check that the bot is alive")
    async def ping(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(format_latency(self.bot.latency))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Core(bot))
