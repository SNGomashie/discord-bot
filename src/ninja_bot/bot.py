from __future__ import annotations

import logging

import discord
from discord.ext import commands

from .config import Config, load_config

log = logging.getLogger("ninja_bot")

EXTENSIONS = [
    "ninja_bot.cogs.core",
    "ninja_bot.cogs.media",
    "ninja_bot.cogs.cs2",
    "ninja_bot.cogs.help",
]


class NinjaBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        # Privileged; only needed to read Toasty replies to ;pokemon.
        intents.message_content = config.toasty_bot_id is not None
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.config = config

    async def setup_hook(self) -> None:
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        if self.config.guild_id is not None:
            guild = discord.Object(id=self.config.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (env=%s)", self.user, self.config.environment)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    NinjaBot(config).run(config.token, log_handler=None)
