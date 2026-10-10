# ninja-discord-bot

Personal Discord bot (discord.py 2.x, Python 3.12+) for ONE private server of friends. Runs in a Proxmox LXC on a homelab. No LLM calls.

## Commands
- Setup: `uv sync` (uses the committed `uv.lock`; add deps with `uv add` / `uv add --dev`)
- Quality gates (must pass before every commit): `./scripts/check.sh` (ruff check, ruff format --check, mypy strict, pytest)
- Run bot: `uv run python -m ninja_bot`
- Live smoke test (needs dev token, manual): `uv run python scripts/smoke.py`

## Architecture
- `src/ninja_bot/bot.py`: `NinjaBot`, loads extensions, syncs slash commands to `GUILD_ID` in dev.
- `src/ninja_bot/config.py`: env-based config (`load_config` takes an optional dict for tests).
- `src/ninja_bot/cogs/`: one cog per feature, each with `async def setup(bot)`; register it in `EXTENSIONS` in bot.py.
- `src/ninja_bot/cogs/media.py`: `/download` cog (thin wrapper; semaphore + cooldown).
- `src/ninja_bot/media/`: download feature logic — `models.py` (VideoInfo, parse_info, MediaError types), `planning.py` (validate_url, plan_download, format_selector), `clip.py` (Clip, parse_timestamp, parse_clip, resolve_clip, format_timestamp), `embed.py` (build_embed), `downloader.py` (Downloader Protocol, YtDlpDownloader — the only module importing yt_dlp).
- `tests/`: pytest (asyncio_mode=auto).

## Conventions
- **Keep logic out of Discord handlers.** Put decisions/formatting/parsing in pure functions or small service classes; cogs are thin wrappers. This is how we test, since bots cannot invoke slash commands.
- Use slash commands (`app_commands`), not prefix commands.
- Type-annotate everything (mypy strict). Persistent data goes under `data/` (gitignored), not in the repo.
- Request only the Discord intents a feature needs.

## Safety
- Never read, print, or commit `.env` or tokens. Dev and prod use different bot tokens; agents only ever have the dev one (`ENVIRONMENT=dev`).
- Don't change deployment/prod configuration unless a story explicitly asks.

## Environment
Dev work and Ralph run in the container from `Dockerfile.dev` (Debian 13, Python 3.13, uv, claude). The venv lives outside the repo (`UV_PROJECT_ENVIRONMENT=/home/dev/.venv`), so the host `.venv` is never shared.

## Commits
Conventional Commits (`type: subject`), small and focused. Story ID goes in the body.

## yt-dlp gotchas
- YouTube needs a JS runtime (deno) and `yt-dlp-ejs` (via the `yt-dlp[default]` extra); otherwise downloads 403. Check with `uv run yt-dlp -v -F <url>` and look for `JS runtimes:`.
- Upgrade regularly: `uv lock --upgrade-package yt-dlp`.
