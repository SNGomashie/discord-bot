"""Live smoke test: logs in with the DEV token, syncs commands to the test guild, exits.

Not part of scripts/check.sh (needs network + token). Run manually: uv run python scripts/smoke.py
"""

import asyncio
import sys

import discord
from dotenv import load_dotenv

from ninja_bot.bot import NinjaBot
from ninja_bot.config import load_config


async def main() -> int:
    load_dotenv()
    config = load_config()
    if not config.is_dev:
        print("Refusing to run smoke test outside ENVIRONMENT=dev")
        return 1
    bot = NinjaBot(config)
    ready = asyncio.Event()

    async def on_ready() -> None:
        ready.set()

    bot.add_listener(on_ready, "on_ready")
    async with bot:
        task = asyncio.create_task(bot.start(config.token))
        try:
            waiter = asyncio.create_task(ready.wait())
            await asyncio.wait({waiter, task}, timeout=30, return_when=asyncio.FIRST_COMPLETED)
            if task.done():  # login failed (bad token, missing intents, ...)
                task.result()
            if not ready.is_set():
                raise TimeoutError
            assert config.guild_id is not None
            cmds = await bot.tree.fetch_commands(guild=discord.Object(config.guild_id))
            print(
                f"OK: logged in as {bot.user}, {len(cmds)} guild command(s): "
                f"{[c.name for c in cmds]}"
            )
            return 0
        except TimeoutError:
            print("FAIL: bot did not become ready in 30s")
            return 1
        finally:
            await bot.close()
            task.cancel()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
