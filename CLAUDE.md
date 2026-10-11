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
- `src/ninja_bot/cogs/media.py`: `/download` cog (thin wrapper; semaphore + `UserCooldown` from `media/cooldown.py`).
- `src/ninja_bot/media/`: download feature logic — `models.py` (VideoInfo, parse_info, MediaError types), `planning.py` (validate_url, plan_download, format_selector), `clip.py` (Clip, parse_timestamp, parse_clip, resolve_clip, format_timestamp), `embed.py` (build_embed), `downloader.py` (Downloader Protocol, YtDlpDownloader — the only module importing yt_dlp).
- `src/ninja_bot/cogs/cs2.py`: `/link`, `/unlink`, `/scoreboard`, `/profile`, `/leaderboard` cog (thin wrapper; shared `UserCooldown`, `CachedStatsProvider(LeetifyClient)` + `SqliteLinkStore` + `SteamCommunityResolver` built in `setup`) plus the auto-post `tasks.loop` (only with `CS2_CHANNEL_ID`; logic in `run_poll_cycle`, driven by `AutoPost(bot, MatchPoller, channel_id, poll_seconds)`).
- `src/ninja_bot/cs2/`: CS2 feature logic (Leetify API):
  - `models.py`: Cs2Error types, Match/PlayerMatchStats/Profile dataclasses, `parse_match`, `parse_profile`, `newest_first` (the one newest-first sort).
  - `steam.py`: `parse_steam_target` (`SteamId` | `VanityName` from a SteamID64, `/profiles/` or `/id/` URL, or bare custom URL name; `is_vanity_name` is the one vanity check), `parse_steam_id` (ID-only wrapper for scripts).
  - `client.py`: `StatsProvider` Protocol (includes `aclose`; wrappers must delegate it), `LeetifyClient` (the only module talking to Leetify; injectable fetch), `full_match` (re-fetches a history entry by id when it lacks players; use it before showing/announcing any history match).
  - `resolver.py`: `SteamResolver` Protocol, `SteamCommunityResolver` (custom URL name -> SteamID64 via `steamcommunity.com/id/<name>/?xml=1`, no API key, regex only; the only module talking to Steam).
  - `cache.py`: `TtlCache` (generic, injectable clock), `CachedStatsProvider` (caches `get_profile` per Steam ID for 5 min; `setup` wraps the `LeetifyClient` in it).
  - `store.py`: `LinkStore` and `MatchLog` Protocols (links; seeded players + posted matches, pruned to the newest `MAX_POSTED_MATCHES`), both implemented by `SqliteLinkStore` in one file (`<DATA_DIR>/cs2.sqlite3`; tables created lazily, blocking calls via `asyncio.to_thread`).
  - `formatting.py`: team tables, `form_strip`, `format_rating`, `rating_bar`, `clean_name` (names in code blocks), `escape_name` (names in embed text), `display_width` (table alignment).
  - `awards.py`: `compute_awards` (fun match awards).
  - `identities.py`: `Identity`, `resolve_identities` (match players -> linked server members via an injected `lookup`; never imports discord). The cog's `member_lookup(guild)` provides it (cache, then REST `fetch_member`).
  - `squad.py`: `/leaderboard` and squad logic: `Metric`, `compute_trend`/`format_trend` (newest 5 vs previous 5 ratings, streaks), `rank_squad` (`SquadEntry` -> `LeaderboardRow`, shared ranks), `SquadMember` (linked member in an announced match).
  - `embed.py`: `build_match_embed` (/scoreboard), `build_announcement_embed` (auto-posts: squad highlights, `Squad` field, `squad_outcome` colour), `build_profile_embed`, `build_leaderboard_embed`.
  - `poller.py`: `MatchPoller` (new matches of linked players for auto-posts: seeds new players silently, age filter, max per cycle, `full_match` + squad; `confirm` marks a match posted after each send attempt).
- `scripts/capture_leetify.py`: manual, dev-only capture of real (anonymized) Leetify responses into `data/leetify-capture/` to check the hand-written fixtures in `tests/fixtures/leetify/`.
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

## Feature workflow
Features are built with the Ralph loop. Repo: https://github.com/SNGomashie/discord-bot (`origin`); `main` is only changed through PRs (docs/workflow tweaks may go straight to main).
1. **Plan** the feature with the user (plan mode / clarifying questions); settle design decisions before writing stories.
2. **PRD**: `ralph-skills:prd` writes `tasks/prd-<feature>.md`. Stories must be small (one Ralph iteration), ordered by dependency, with criteria verifiable by `./scripts/check.sh`. Anything only checkable inside Discord is a manual check, not a gate.
3. **Convert**: `ralph-skills:ralph` writes `scripts/ralph/prd.json` (branch `ralph/<feature>`; Ralph files live in `scripts/ralph/`, not the repo root). First archive the previous run's `prd.json` and `progress.txt` to `scripts/ralph/archive/YYYY-MM-DD-<feature>/` (gitignored) and start a fresh `progress.txt` that keeps the "Codebase Patterns" section. Create the branch from up-to-date `main` and commit the PRD + `prd.json` there.
4. **Run** in the dev container (`docker compose run --rm dev`; podman: `PODMAN_USERNS=keep-id podman compose run --rm dev`; log in once with `claude`): `scripts/ralph/ralph.sh <stories + 2-3>`. It refuses unless `.env` has `ENVIRONMENT=dev`. Ralph commits as "ralph", one commit per story. Rebuild the image if `Dockerfile.dev` changed.
5. **Review and test**: run `/code-review`, then do the "needs manual check" items from `progress.txt` against the dev bot in the test server (`uv run python scripts/smoke.py`, then `/command`).
6. **PR** from `ralph/<feature>` to `main` on GitHub (summary, what changed, manual checks, known follow-ups). Keep the per-story commits (no squash). Delete the branch after merge.
Review follow-ups get their own PRD/branch (e.g. `ralph/download-hardening`).

## Commits
Conventional Commits (`type: subject`), small and focused. Story ID goes in the body. Commits are authored as Sena Gomashie <40298891+SNGomashie@users.noreply.github.com> (repo-local git config; set it if missing). Commits made by Claude end with the `Co-Authored-By: Claude ...` trailer; PR descriptions end with the "Generated with Claude Code" line.

## Deployment (not automated yet)
Prod runs in a Debian 13 LXC on Proxmox with the prod token (`ENVIRONMENT=prod`, no `GUILD_ID`). It needs ffmpeg and deno. Setup, permissions, systemd unit and update steps are in `docs/deployment.md`; keep it current when adding env vars, intents or system dependencies. Agents never touch prod.

## yt-dlp gotchas
- YouTube needs a JS runtime (deno) and `yt-dlp-ejs` (via the `yt-dlp[default]` extra); otherwise downloads 403. Check with `uv run yt-dlp -v -F <url>` and look for `JS runtimes:`.
- Upgrade regularly: `uv lock --upgrade-package yt-dlp`; bump the pinned deno tag in `Dockerfile.dev` (currently 2.9.7) alongside it when needed.
- mp4 requests are always remuxed to mp4 (`FFmpegVideoRemuxer`), so webm/mkv sources still produce `.mp4`.
- The mp4 quality ladder only contains distinct heights the video actually has (`height_ladder` in planning.py), so no rung re-downloads the same format.
