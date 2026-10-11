# ninja-discord-bot

Personal Discord bot (discord.py 2.x, Python 3.12+) for ONE private server of friends. Runs in a Proxmox LXC on a homelab. No LLM calls.

## Commands
- Setup: `uv sync` (uses the committed `uv.lock`; add deps with `uv add` / `uv add --dev`)
- Quality gates (must pass before every commit): `./scripts/check.sh` (ruff check, ruff format --check, mypy strict, pytest)
- Run bot: `uv run python -m ninja_bot`
- Live smoke test (needs dev token, manual): `uv run python scripts/smoke.py`

## Architecture
- `src/ninja_bot/bot.py`: `NinjaBot`, loads extensions, syncs slash commands to `GUILD_ID` when it is set (required in dev), globally otherwise.
- `src/ninja_bot/config.py`: env-based config (`load_config` takes an optional dict for tests).
- `src/ninja_bot/posting.py`: shared auto-post plumbing for the cs2 and activity cogs: `send_safely(send, what)` (logs and swallows `HTTPException`, returns whether it was sent) and `PostChannelResolver(bot, warning_template, channel_id).resolve()` (`get_channel` -> `fetch_channel` (NotFound/Forbidden = `CHANNEL_MISSING`, other HTTP errors = `CHANNEL_UNAVAILABLE`) -> `is_post_channel` -> `channel_problem`; warns once per cause, cleared when the channel works).
- `src/ninja_bot/cogs/`: one cog per feature, each with `async def setup(bot)`; register it in `EXTENSIONS` in bot.py.
- `src/ninja_bot/cogs/core.py`: `/ping`.
- `src/ninja_bot/cogs/help.py`: `/help` cog (thin wrapper: `command_infos` turns `bot.tree.get_commands()` into `CommandInfo`, `help_reply` picks overview/detail/unknown text); logic in `src/ninja_bot/help/` (`content.py` all help text, `commands.py`, `embed.py`, `lookup.py`).
- `src/ninja_bot/cogs/media.py`: `/download` cog (thin wrapper; semaphore + `UserCooldown` from `media/cooldown.py`).
- `src/ninja_bot/media/`: download feature logic — `models.py` (VideoInfo, parse_info, MediaError types), `planning.py` (validate_url, plan_download, format_selector), `clip.py` (Clip, parse_timestamp, parse_clip, resolve_clip, format_timestamp), `embed.py` (build_embed), `downloader.py` (Downloader Protocol, YtDlpDownloader — the only module importing yt_dlp).
- `src/ninja_bot/cogs/cs2.py`: `/link`, `/unlink`, `/scoreboard`, `/profile`, `/leaderboard` cog (thin wrapper; shared `UserCooldown`, `CachedStatsProvider(LeetifyClient)` + `SqliteLinkStore` + `SteamCommunityResolver` built in `setup`) plus the auto-post `tasks.loop` (only with `CS2_CHANNEL_ID`; logic in `run_poll_cycle`, driven by `AutoPost(bot, MatchPoller, channel_id, poll_seconds)`).
- `src/ninja_bot/cs2/`: CS2 feature logic (Leetify API):
  - `models.py`: Cs2Error types, Match/PlayerMatchStats/Profile dataclasses, `parse_match`, `parse_profile`, `newest_first` (the one newest-first sort).
  - `steam.py`: `parse_steam_target` (`SteamId` | `VanityName` from a SteamID64, `/profiles/` or `/id/` URL, or bare custom URL name; `is_vanity_name` is the one vanity check), `parse_steam_id` (ID-only wrapper for scripts).
  - `client.py`: `StatsProvider` Protocol (includes `aclose`; wrappers must delegate it), `LeetifyClient` (the only module talking to Leetify; injectable fetch), `full_match` (re-fetches a history entry by id when it lacks players; use it before showing/announcing any history match; `strict=True` re-raises lookup errors instead of falling back to the partial entry, and anything that announces a match must use it).
  - `resolver.py`: `SteamResolver` Protocol, `SteamCommunityResolver` (custom URL name -> SteamID64 via `steamcommunity.com/id/<name>/?xml=1`, no API key, regex only; the only module talking to Steam; bodies are read with `read_capped`, never a single `content.read(n)`).
  - `cache.py`: `TtlCache` (generic, injectable clock), `CachedStatsProvider` (caches `get_profile` per Steam ID for 5 min; `setup` wraps the `LeetifyClient` in it).
  - `store.py`: `LinkStore` and `MatchLog` Protocols (links; seeded players + posted matches, pruned to the newest `MAX_POSTED_MATCHES`), both implemented by `SqliteLinkStore` in one file (`<DATA_DIR>/cs2.sqlite3`; tables created lazily, blocking calls via `asyncio.to_thread`).
  - `formatting.py`: team tables, `form_strip`, `format_rating`, `rating_bar`, `clean_name` (names in code blocks), `escape_name` (names in embed text), `display_width` (table alignment).
  - `awards.py`: `compute_awards` (fun match awards).
  - `identities.py`: `Identity`, `resolve_identities` (match players -> linked server members via an injected `lookup`; never imports discord). The cog's `member_lookup(guild)` provides it (cache, then REST `fetch_member`).
  - `squad.py`: `/leaderboard` and squad logic: `Metric`, `compute_trend`/`format_trend` (newest 5 vs previous 5 ratings, streaks), `rank_squad` (`SquadEntry` -> `LeaderboardRow`, shared ranks), `fetch_squad`/`leaderboard_notes` (the /leaderboard fetch and skip policy), `SquadMember` (linked member in an announced match).
  - `embed.py`: `build_match_embed` (/scoreboard), `build_announcement_embed` (auto-posts: squad highlights, `Squad` field, `squad_outcome` colour), `build_profile_embed`, `build_leaderboard_embed`.
  - `channels.py`: `channel_problem(channel, me)` (can the bot auto-post there? cause strings `CHANNEL_*` incl. `CHANNEL_UNAVAILABLE` used by `posting.py`, `PostChannel`, `is_post_channel`; no network).
  - `poller.py`: `MatchPoller` (new matches of linked players for auto-posts: seeds new players silently, age filter, max per cycle, `full_match` + squad; `confirm` marks a match posted after each send attempt).
- `src/ninja_bot/cogs/activity.py`: voice tracking + `/activity`, `/myactivity` cog (thin wrapper: the commands use `resolve_month` (periods.py), a shared `UserCooldown` refunded unless posted, reply without defer (errors ephemeral, embeds public) and `monthly_report(month, guild)` (store -> `build_monthly_report`, then `without_departed` with the shame candidates that cs2 `member_lookup` confirms left the guild, lowest first until `SHAME_LIMIT` are kept); `on_voice_state_update` applies `plan_transition` via the store, 60 s heartbeat `tasks.loop`, first `on_ready` runs `reconcile` (`close_stale`, then fresh sessions for members in voice) and starts the heartbeat; injectable clock).
- `src/ninja_bot/activity/`: activity tracking logic:
  - `tracker.py`: `plan_transition(before, after, afk) -> Open | Close | Move | Nothing` (AFK channel = not in voice; no discord import).
  - `periods.py`: `Month` (the one month definition), `month_of`, `parse_month`, `resolve_month` (blank = current month, future rejected), `InvalidMonthError`, `split_local` (the one DST-safe split of an interval at local hour/day boundaries).
  - `stats.py`: `clip`, `monthly_stats` -> `UserStats` per user (totals, active days with `ACTIVE_DAY_MIN_SECONDS`, streaks, session counts; local days via the tz, real seconds across DST), `rank` (shared ranks), `shame_candidates` (first voice session before the month end, last within `SHAME_LOOKBACK_DAYS` of it), `shame_list` (lowest totals among candidates, 0 included).
  - `awards.py`: `compute_awards(stats, previous_stats, sessions, month, tz, now)` -> `Award(key, user_id, value, label)` (night owl, marathon, most consistent, climber/drop, early bird; ties give one `Award` per user, value 0 omitted), `social_stats` -> `SocialStats` (`top_pair` via a per-channel sweep, busiest local hour/weekday, server total); `_hour_pieces` wraps `split_local`.
  - `report.py`: `MonthlyReport` / `build_monthly_report(sessions, previous_sessions, voice_user_ranges, month, tz, now, pokemon_events=())` (stats, shared-rank leaderboard, `SHAME_LIMIT` shame list + full `shame_pool`, awards, social, pokemon), `drop_departed`/`without_departed` (pure departed-member filter) and `UserProfile` / `user_profile(report, user_id, display_name)` (None without voice time).
  - `embed.py`: `build_monthly_embed` (Leaderboard fields packed with cs2 `pack_lines`, max `MAX_LEADERBOARD_ROWS` + `+N more`, Wall of shame, Awards, Together, Pokémon (only with Pokémon data, also on an empty month); empty month -> `EMPTY_MONTH_MESSAGE`; footer `Times in <tz>`), `build_profile_embed` (title name via `escape_name`; Pokémon field when the month has Pokémon data). Send with `AllowedMentions.none()`.
  - `formatting.py`: `format_hours`, `day_strip`.
  - `reporter.py`: `MonthlyReporter(store, tz, clock)`: `due_month()` (oldest ended, unposted month with voice time, never before the first session; one per call), `confirm(month)` after every send attempt. The cog's hourly `report_loop` (only with `ACTIVITY_CHANNEL_ID`) runs `run_report_cycle` (channel checked with cs2 `channel_problem`; channel problems warn once and leave the month unposted).
  - `pokemon_stats.py`: `pokemon_stats(events, month, tz)` -> `PokemonStats` (per user `PokemonUserStats` catches/shinies/cooldown_hits, `top_species` with ties, `shinies` oldest first), `impatient_awards` (`AwardKey.IMPATIENT`, needs `IMPATIENT_MIN_COOLDOWNS`). `build_monthly_report(..., pokemon_events)` adds them to `MonthlyReport.pokemon`/`awards`; the overview shows Impatient in the `Pokémon` field (`POKEMON_AWARDS`), not in Awards.
  - `pokemon.py`: Toasty `;pokemon` logic (no discord import): `is_pokemon_command`, `parse_reply(content, embeds) -> Catch | Cooldown | None` (content + embed title/description/field values; fixtures in `tests/fixtures/toasty/replies.json`), `parse_duration`, `to_event` (Catch | Cooldown -> `PokemonEvent`), `PendingCommands` (command id -> author, 5 min TTL, capped). The `PokemonTracker` cog in cogs/activity.py (added only with `TOASTY_BOT_ID`) pairs Toasty replies via `reference.message_id` and stores only parsed fields.
  - `store.py`: `ActivityStore` Protocol, `Session` and `PokemonEvent` dataclasses, `SqliteActivityStore` (`<DATA_DIR>/activity.sqlite3`: `voice_sessions` with `last_seen` heartbeats, `reports_posted`, `pokemon_events` keyed by the reply's message id (duplicates ignored); `voice_user_ranges` (first/last session start per voice user, Pokémon-only users excluded); same lazy-schema/`asyncio.to_thread` pattern as `cs2/store.py`; `open_session` closes the user's open session first).
- `src/ninja_bot/sessions/`: session poll logic (no discord import):
  - `slots.py`: `InvalidSessionInput` (user-facing `.message`), `parse_days` (`fri,sat`, full names, `weekend`/`weekdays` -> sorted Mon=0 weekdays), `parse_times` (24-hour `H:MM`/`HH:MM` -> sorted naive `time`s); inputs capped at `MAX_DAYS_LENGTH`/`MAX_TIMES_LENGTH` (64) before splitting, ASCII digits only; `Slot(start, end)` (UTC seconds), `parse_start_date` (blank = tomorrow in tz, past rejected), `generate_slots` (window `[start, start + weeks)`, slots not after `now` dropped, DST gap skipped / fold=0, empty or > `MAX_SLOTS` (25) raises; `MAX_WEEKS` is checked by the caller), `next_window_start` (next round's window).
  - `scoring.py`: `Vote` (`YES`/`IF_NEEDED`/`NO`), `SlotScore`, `score_slots` (joined players only; no vote = `missing`), `evaluate_round` -> `Fits(top)` | `NextRound` | `NoFit` (fits = `attending >= min_players or len(players)` and the GM votes Yes/If needed; top 3 by yes desc, if-needed asc, slot id), `pending_players`/`all_voted` (a "none of these" voter has `NO` votes, so is not pending); `MAX_ROUNDS` (6), `MAX_PLAYERS` (25).
  - `store.py`: `Poll`/`PollSlot`/`Session` dataclasses, status constants (`STATUS_*`, `OPEN_STATUSES`), `SessionStore` Protocol, `SqliteSessionStore` (`<DATA_DIR>/sessions.sqlite3`: `polls` (days/times as comma-separated text), `poll_slots`, `poll_players` (join order = rowid; GM auto-joined by `create_poll`), `poll_votes` (`votes(poll, round)` only returns that round's slots), `sessions` (roster as comma-separated text, `cancelled` flag), `session_reminders` (`INSERT OR IGNORE` markers), `poll_nudges` (counted per round); `advance_round`/`confirm` take explicit `now`/slot and are atomic; same lazy-schema/`asyncio.to_thread` pattern as `activity/store.py`).
  - `content.py`: all user-facing session poll strings (constants only; no discord/ninja_bot imports).
  - `service.py`: `SessionService(store, tz, clock, prefiller)`: `create_poll` (validates name <= `MAX_NAME_LENGTH` 60, weeks, `duration_hours` <= `MAX_DURATION_HOURS` 12, `min_players` 2..25, `round_hours` 1..`MAX_ROUND_HOURS` 336, `MAX_OPEN_POLLS` (5) per guild; raises `InvalidSessionInput`; unset `min_players`/`round_hours` are stored as 0), `join`/`leave` -> `JoinResult`, `submit_vote(poll, user, yes_ids, maybe_ids)` -> `VoteResult` (replaces the whole round; both lists = YES, neither = NO); `JOIN_MESSAGES`/`VOTE_MESSAGES` map results to content strings; `Prefiller` Protocol (`NoPrefill`), applied on join without overwriting stored votes. `close_round(poll, user, is_manager, reason)` -> `RoundOutcome` (`RoundFits` -> `awaiting_confirm` | `RoundAdvanced` (next window via `next_window_start`) | `RoundFailed` (`FailReason.LAST_ROUND`/`NO_SLOTS`) | `NotVoting` (already closed/unknown: idempotent) | `NotAllowed`; only `"manual"` checks GM/manager), `confirm` (re-evaluates the frozen round; roster = Yes/If needed voters in join order), `cancel_poll`/`cancel_session`; refusals raise `SessionRefused(.message)`; an `asyncio.Lock` serialises close/confirm/cancel_poll. `should_auto_close` -> `"all_voted"` | `"deadline"` | None.
- `scripts/capture_leetify.py`: manual, dev-only capture of real (anonymized) Leetify responses into `data/leetify-capture/` to check the hand-written fixtures in `tests/fixtures/leetify/`.
- `tests/`: pytest (asyncio_mode=auto).

## Conventions
- **Keep logic out of Discord handlers.** Put decisions/formatting/parsing in pure functions or small service classes; cogs are thin wrappers. This is how we test, since bots cannot invoke slash commands.
- Use slash commands (`app_commands`), not prefix commands.
- Type-annotate everything (mypy strict). Persistent data goes under `data/` (gitignored), not in the repo.
- Request only the Discord intents a feature needs. The only privileged one is Message Content, requested only when `TOASTY_BOT_ID` is set (`bot.py`); nothing else may depend on it.
- All `/help` text lives in `help/content.py` (`src/ninja_bot/help/content.py`); other help modules and the cog hold no user-facing help strings.

## Safety
- Never read, print, or commit `.env` or tokens. Dev and prod use different bot tokens; agents only ever have the dev one (`ENVIRONMENT=dev`).
- Don't change deployment/prod configuration unless a story explicitly asks.

## Environment
Dev work and Ralph run in the container from `Dockerfile.dev` (Debian 13, Python 3.13, uv, claude). The venv lives outside the repo (`UV_PROJECT_ENVIRONMENT=/home/dev/.venv`), so the host `.venv` is never shared.

## Feature workflow
Features are built with the Ralph loop. Repo: https://github.com/SNGomashie/discord-bot (`origin`); `main` is only changed through PRs (docs/workflow tweaks may go straight to main).
1. **Plan** the feature with the user (plan mode / clarifying questions); settle design decisions before writing stories.
2. **PRD**: `ralph-skills:prd` writes `tasks/prd-<feature>.md`. Stories must be small (one Ralph iteration), ordered by dependency, with criteria verifiable by `./scripts/check.sh`. Anything only checkable inside Discord is a manual check, not a gate. Every story that changes user-visible behaviour, settings, permissions, dependencies or storage must list the docs it updates (see Documentation) in its acceptance criteria; add a final docs story when a feature spans several stories (README feature section, `docs/manual-checks.md`).
3. **Convert**: `ralph-skills:ralph` writes `scripts/ralph/prd.json` (branch `ralph/<feature>`; Ralph files live in `scripts/ralph/`, not the repo root). First archive the previous run's `prd.json` and `progress.txt` to `scripts/ralph/archive/YYYY-MM-DD-<feature>/` (gitignored) and start a fresh `progress.txt` that keeps the "Codebase Patterns" section. Create the branch from up-to-date `main` and commit the PRD + `prd.json` there.
4. **Run** in the dev container (`docker compose run --rm dev`; podman: `PODMAN_USERNS=keep-id podman compose run --rm dev`; log in once with `claude`): `scripts/ralph/ralph.sh <stories + 2-3>`. It refuses unless `.env` has `ENVIRONMENT=dev`. Ralph commits as "ralph", one commit per story. Rebuild the image if `Dockerfile.dev` changed.
5. **Review and test**: run `/code-review`, check that the README (and the other docs in the Documentation table) match the final code, then do the "needs manual check" items from `progress.txt` against the dev bot in the test server (`uv run python scripts/smoke.py`, then `/command`).
6. **PR** from `ralph/<feature>` to `main` on GitHub (summary, what changed, manual checks, known follow-ups), including the `CHANGELOG.md` entry. Keep the per-story commits (no squash). Delete the branch after merge.
Review follow-ups get their own PRD/branch (e.g. `ralph/download-hardening`).

## Documentation
Docs are part of the change, not a follow-up. **A change is not done until the docs that describe it are updated in the same branch** (for Ralph: in the same story/commit as the code, never "later"). `README.md` is the single source of truth for what the bot does; it is deliberately long and complete, so don't move behaviour into `docs/` to shorten it.

| If the change touches... | Update |
|---|---|
| A command, parameter, choice, cooldown, limit, constant (e.g. `MAX_PER_CYCLE`, `TREND_WINDOW`), user-visible message, embed field, or error reply | `README.md`: the commands table **and** the feature section |
| An env var / `config.py` | `README.md` Configuration table, `.env.example`, `docs/deployment.md` settings reference |
| An intent, bot permission or channel requirement | `README.md` Configuration (and auto-post section if relevant), `docs/deployment.md` Permissions |
| A system dependency or pinned version (ffmpeg, deno, Python, uv) | `README.md` system dependencies, `Dockerfile.dev`, `docs/deployment.md`, and this file's yt-dlp gotchas (deno is pinned in several places: grep for the old version) |
| Persisted data (new table/column/file) | `README.md` Data and storage, backup notes in `docs/deployment.md` |
| A new or removed known gap / workaround | `README.md` Known limitations |
| Module layout (new file, moved responsibility) | this file's Architecture list and the layout block in `README.md` |
| A new script or workflow step | `README.md` Development |
| A new/changed/removed slash command | also `help/content.py` (its `CommandHelp` entry; `tests/test_help_drift.py` checks it and the README table) |
| Something only checkable in Discord | `docs/manual-checks.md` (not the README, not only `progress.txt`) |
| Ops / deployment / troubleshooting only | `docs/deployment.md`; link to the README rather than repeating behaviour |

Rules:
- **Verify before writing.** Take numbers, formats and messages from the code (read the constant or string), not from memory, a PRD or an old PR description. If code and docs disagree, the code wins: fix the docs and say so in the commit.
- **Remove what's gone.** When behaviour is removed or changed, delete or rewrite the old text; grep the docs for the old command name, constant or value.
- **New command = new docs.** Add the command to the README commands table and a section covering parameters, accepted formats, cooldown/refund rules, reply visibility, errors and caveats. Also give it a clear Discord-side `description=` and parameter descriptions (the in-bot docs).
- `CHANGELOG.md`: add one entry per feature/fix PR (newest first, under the merge date) when you write the PR, summarising what shipped and known follow-ups.
- `docs/deployment.md` is ops only (Discord app, LXC, systemd, updates, troubleshooting). `docs/manual-checks.md` holds the Discord-only checklists per feature.
- Keep README links and anchors working (headings are linked from `docs/` and the contents list); after editing, check that relative links resolve.
- Docs-only changes may go straight to `main` (see Feature workflow); they still need `./scripts/check.sh` to pass when they touch `.env.example` or code.

## Commits
Conventional Commits (`type: subject`), small and focused. Story ID goes in the body. Commits are authored as Sena Gomashie <40298891+SNGomashie@users.noreply.github.com> (repo-local git config; set it if missing). Commits made by Claude end with the `Co-Authored-By: Claude ...` trailer; PR descriptions end with the "Generated with Claude Code" line.

## Deployment (not automated yet)
Prod runs in a Debian 13 LXC on Proxmox with the prod token (`ENVIRONMENT=prod`, no `GUILD_ID`). It needs ffmpeg and deno. Setup, permissions, systemd unit and update steps are in `docs/deployment.md`; keep it current when adding env vars, intents or system dependencies. Agents never touch prod.

## yt-dlp gotchas
- YouTube needs a JS runtime (deno) and `yt-dlp-ejs` (via the `yt-dlp[default]` extra); otherwise downloads 403. Check with `uv run yt-dlp -v -F <url>` and look for `JS runtimes:`.
- Upgrade regularly: `uv lock --upgrade-package yt-dlp`; bump the pinned deno tag in `Dockerfile.dev` (currently 2.9.7) alongside it when needed.
- mp4 requests are always remuxed to mp4 (`FFmpegVideoRemuxer`), so webm/mkv sources still produce `.mp4`.
- The mp4 quality ladder only contains distinct heights the video actually has (`height_ladder` in planning.py), so no rung re-downloads the same format.
