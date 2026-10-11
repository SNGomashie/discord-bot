# PRD: Activity tracking (voice hours, monthly report, Toasty catches)

## Introduction

The server's friends spend a lot of time in voice, but nobody can see how much, who shows up consistently, or who has gone quiet. This feature makes the bot track **voice time** per member, and each month produce a leaderboard / "wall of shame" / overview with consistency stats and fun awards. Members also use the **Toasty** bot to catch Pokémon (`;pokemon`); the bot observes those commands and replies to add catch stats to the overview.

This PRD adds:

- A voice tracker: time connected to voice channels, excluding the server's AFK channel, stored in SQLite. It survives bot restarts and crashes (open sessions are heartbeated and reconciled on startup).
- `/activity [month]`: server overview for a month (leaderboard, shame, awards, social stats, Pokémon stats). Public reply.
- `/myactivity [user] [month]`: personal view (hours, active days, streaks, day strip, awards, catches). Public reply.
- An automatic monthly post of the previous month's overview to `ACTIVITY_CHANNEL_ID`, once per month, restart-safe.
- Toasty tracking (only when `TOASTY_BOT_ID` is set): a human `;pokemon` message followed by a Toasty reply to it is recorded as a catch for the human. This needs the privileged **Message Content** intent, which is requested only when `TOASTY_BOT_ID` is set.

"Hours" means voice time. Text messages are not counted. No LLM calls.

## Goals

- Anyone can see their own and everyone else's voice hours for any month, from Discord.
- Once a month the channel gets a report without anyone running a command, and a restart never causes a missing or duplicate report.
- No voice time is lost or double-counted across bot restarts, crashes, channel moves, midnight, month boundaries and DST changes.
- The report is fun: shared ranks, a shame list that includes members with 0h, consistency stats and awards.
- The privileged Message Content intent is requested only when Toasty tracking is turned on; the bot starts and works without it.
- All decisions (clipping to months, streaks, awards, ranks, report due-ness) live in pure, tested functions; the cog is a thin wrapper.

## User Stories

### US-001: Config and conditional message-content intent
**Description:** As the owner, I want the activity settings in config and the privileged intent requested only when needed, so that the bot still starts as before when the feature is not configured.

**Acceptance Criteria:**
- [ ] `Config` gains `activity_channel_id: int | None = None`, `activity_timezone: str = "UTC"` and `toasty_bot_id: int | None = None` (all with defaults, since `tests/test_help_drift.py` builds `Config(...)` directly)
- [ ] `load_config` reads `ACTIVITY_CHANNEL_ID` and `TOASTY_BOT_ID` with `_parse_optional_positive_int` and `ACTIVITY_TIMEZONE` (default `UTC`)
- [ ] An `ACTIVITY_TIMEZONE` that `zoneinfo.ZoneInfo` rejects raises `ConfigError` naming the variable; empty string means the default
- [ ] `NinjaBot` sets `intents.message_content = config.toasty_bot_id is not None`; no other intent changes
- [ ] Tests in `tests/test_config.py`: defaults, valid values, invalid channel id, invalid bot id, invalid timezone (parametrised); a test that the intent is on only when `toasty_bot_id` is set
- [ ] Docs: `README.md` Configuration table (three new rows) and the "no privileged intent" sentence (rewrite: only needed with `TOASTY_BOT_ID`, must also be enabled in the Developer Portal), `.env.example` (commented examples), `docs/deployment.md` settings reference and its intents/permissions text and checklist item "No privileged intents"
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes (ruff, ruff format, mypy strict, pytest)

### US-002: Month periods and duration formatting
**Description:** As a developer, I need timezone-aware month windows and a readable hours format so that all later stories share one definition of "a month".

**Acceptance Criteria:**
- [ ] New package `src/ninja_bot/activity/` (with `__init__.py`); `periods.py` has no `discord` import
- [ ] `Month` frozen dataclass `(year, month)` with `key` (`"2026-09"`), `start(tz)` / `end(tz)` returning aware datetimes (end is the first instant of the next month, exclusive), `days(tz)` (number of local days), and `previous()` / `next()`
- [ ] `month_of(timestamp: float, tz: ZoneInfo) -> Month` and `parse_month(text: str) -> Month` (accepts `YYYY-MM`; raises a `ValueError` subclass with a user-readable message otherwise; rejects month 0 and 13)
- [ ] A month in a DST-changing timezone has the correct start/end instants (test with `Europe/Amsterdam` for March and October 2026)
- [ ] `activity/formatting.py` (no `discord` import): `format_hours(seconds: float) -> str` (`0m`, `45m`, `1h 05m`, `12h 30m`, `103h 07m`; truncates, never negative) and `day_strip(active_days: set[int], days_in_month: int) -> str` (🟩 active / ⬜ not, one cell per day, a space every 7 days)
- [ ] Unit tests for each function including February in a leap and non-leap year, year rollover (December → January), invalid month text, and 0/negative seconds
- [ ] Docs: none beyond code (internal helpers); no user-visible change yet
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-003: Activity store
**Description:** As a developer, I need persistent storage of voice sessions and posted reports so that tracking survives restarts.

**Acceptance Criteria:**
- [ ] `activity/store.py` defines the `ActivityStore` Protocol and `SqliteActivityStore(path)` implementing it, following `cs2/store.py` (`_SCHEMA` tuple of `CREATE TABLE IF NOT EXISTS`, lazy `_connect` creating the parent dir, blocking calls via `asyncio.to_thread`, `closing(...)` per call)
- [ ] Table `voice_sessions(id INTEGER PRIMARY KEY, user_id, channel_id, start_ts REAL, end_ts REAL NULL, last_seen REAL)` plus an index on `(user_id, start_ts)`; an open session has `end_ts` NULL
- [ ] Table `reports_posted(month_key TEXT PRIMARY KEY, posted_at REAL)`
- [ ] Protocol methods: `open_session(user_id, channel_id, now)`, `close_session(user_id, now)` (closes that user's open session; no-op if none), `heartbeat(now)` (sets `last_seen` on all open sessions), `close_stale(default_end: float | None)` (closes every open session at its `last_seen`), `open_sessions() -> list[Session]`, `sessions_between(start_ts, end_ts) -> list[Session]` (any session overlapping the window, open ones included), `known_users() -> set[int]` (every user ever seen), `mark_report_posted(month_key, now)`, `report_posted(month_key) -> bool`
- [ ] A user never has two open sessions: `open_session` closes any existing open session for that user at `now` first, in the same transaction
- [ ] Tests with `tmp_path` real SQLite: Protocol conformance, round trip, persistence across a new instance, missing parent dir, overlap query (inside, spanning start, spanning end, outside, open), `close_stale` uses `last_seen`, `asyncio.to_thread` recorder test like `tests/test_cs2_store.py`
- [ ] Docs: `README.md` "Data and storage" (new `activity.sqlite3` and table purposes; fix the sentence "The only persistent data is the CS2 database"), backup notes in `docs/deployment.md`
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-004: Voice tracker and cog listener
**Description:** As a member, I want my voice time recorded automatically, including after bot restarts.

**Acceptance Criteria:**
- [ ] `activity/tracker.py` (no `discord` import): pure `plan_transition(before_channel: int | None, after_channel: int | None, afk_channel: int | None) -> Action` returning `Open(channel)`, `Close`, `Move(channel)` or `Nothing`; joining the AFK channel counts as leaving; leaving the AFK channel counts as joining; mute/deafen/stream changes within the same channel are `Nothing`
- [ ] `cogs/activity.py` with `async def setup(bot)` building `SqliteActivityStore(Path(bot.config.data_dir) / "activity.sqlite3")`; registered in `EXTENSIONS` in `bot.py`
- [ ] `on_voice_state_update` ignores bots and applies the action via the store (`Move` = close then open); errors are logged and never raised
- [ ] A 60-second heartbeat `tasks.loop` calls `store.heartbeat(now)`; `before_loop` waits for ready
- [ ] On start (after ready): `close_stale()` closes sessions left from a previous run at their `last_seen`, then members currently in non-AFK voice channels of the guild (`GUILD_ID` when set, else every guild) get fresh sessions
- [ ] `cog_unload` cancels loops; clock is injectable (`Callable[[], float]`, default `time.time`) as in `cs2/poller.py`
- [ ] Tests: transition table (join, leave, move, join AFK, leave AFK, mute only, bot user), cog listener with fake store and mocked members, reconcile after a simulated crash (session closed at `last_seen`, not at restart time), no double sessions
- [ ] The existing drift-test fixture still loads all extensions offline with default config
- [ ] Docs: `README.md` (short "Activity tracking" section: what is counted, AFK excluded, restart behaviour, `voice_states` intent only), layout block, `docs/manual-checks.md` section "Activity tracking" (join / leave / move / AFK / restart mid-session)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-005: Core monthly stats
**Description:** As a member, I want accurate monthly totals, active days and streaks so that the leaderboard is trustworthy.

**Acceptance Criteria:**
- [ ] `activity/stats.py` (pure, no `discord`/IO): `Session` is the dataclass from the store module; `clip(session, start, end, now)` clips to the month window and treats an open session as ending at `now`; zero or negative durations are dropped
- [ ] `monthly_stats(sessions, month, tz, now) -> dict[int, UserStats]` where `UserStats` has `total_seconds`, `active_days: frozenset[int]` (local day-of-month; a session crossing midnight marks both days), `longest_streak`, `current_streak` (streak ending on the last day of the month, or ending today when the month is current), `sessions_count`, `longest_session_seconds`, `average_session_seconds`
- [ ] A day counts as active when the user has at least 5 minutes of voice that local day (constant `ACTIVE_DAY_MIN_SECONDS = 300`)
- [ ] `rank(entries: Sequence[tuple[int, float]]) -> list[RankedRow]` with shared ranks for ties (1, 1, 3), ordered by value descending then user id for stability
- [ ] `shame_list(totals, known_users, limit)` returns the lowest totals among `known_users`, including users with 0 seconds in the month, ordered ascending; users who have never been seen are not invented
- [ ] Tests on fixed windows (`datetime(2026, 10, ...)`): session inside month, spanning month start, spanning month end, spanning midnight, across a DST change in `Europe/Amsterdam`, open session ending at `now`, ties, 0h shame, streak rules, 5-minute threshold
- [ ] Docs: none yet (surfaced in US-008; internal)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-006: Awards and social stats
**Description:** As a member, I want fun awards and social stats so that the overview is entertaining, not just a table.

**Acceptance Criteria:**
- [ ] `activity/stats.py` (or `activity/awards.py` if the file gets large) adds pure `compute_awards(stats, previous_stats, sessions, month, tz) -> list[Award]` with `Award(key, user_id, value, label)`: **Night owl** (most voice time between 00:00 and 06:00 local), **Marathon** (longest single session), **Most consistent** (most active days, ties by total), **Biggest climber** and **Biggest drop** (month-over-month change in hours; only when the previous month had data), **Early bird** (most time between 06:00 and 09:00 local)
- [ ] An award is omitted when nobody qualifies (value 0) or on a tie it names all tied users; labels come from named constants
- [ ] `social_stats(sessions, month, tz) -> SocialStats`: `top_pair` (two users with the most overlapping seconds in the same channel, ignoring pairs under 10 minutes), `busiest_hour` and `busiest_weekday` for the server (by total person-seconds), `total_voice_seconds` for the server
- [ ] Overlap is computed per channel using clipped intervals and is O(n log n) in sessions per channel; 10 000 sessions must run in under one second in a test
- [ ] Tests for each award (winner, tie, none qualifying, no previous month), overlap (partial, none, same user twice), busiest hour/weekday, a session crossing midnight into the night-owl window
- [ ] Docs: none yet (surfaced in US-008; internal)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-007: Overview and profile embeds
**Description:** As a member, I want the leaderboard, shame list, awards and my own stats rendered nicely within Discord's limits.

**Acceptance Criteria:**
- [ ] `_pack_lines` in `cs2/embed.py` is made public (`pack_lines`) and imported by the new module; existing cs2 tests keep passing
- [ ] `activity/embed.py`: `build_monthly_embed(report: MonthlyReport) -> discord.Embed` with fields: **Leaderboard** (medals 🥇🥈🥉 for ranks 1–3, `<@id> **12h 30m** · 18 days`), **Wall of shame** (lowest hours, includes 0h), **Awards**, **Together** (top pair, busiest hour/weekday, server total), and, when catch data is present, **Pokémon** (added in US-011); title carries the month (`Voice activity · September 2026`)
- [ ] `build_profile_embed(profile: UserProfile) -> discord.Embed`: total hours with rank, active days of N, current and longest streak, longest session, average session, `day_strip`, awards won, and catches when present
- [ ] Names inside embed text go through `escape_name`; every field value at most 1024 characters, title at most 256, total at most 6000, at most 25 fields; leaderboard capped at 25 rows with a `+N more` footer; empty month shows an empty-state description instead of an empty embed
- [ ] The embed footer shows the timezone (`Times in Europe/Amsterdam`)
- [ ] Tests: full report fits all limits with 40 users and long names, empty month, single user, ties share a rank, `+N more` footer, no field with an empty value
- [ ] Docs: none yet (surfaced in US-008)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-008: `/activity` and `/myactivity` commands
**Description:** As a member, I want to look at the server's or my own voice stats for any month.

**Acceptance Criteria:**
- [ ] `/activity` with optional `month` (text `YYYY-MM`, default current month in `ACTIVITY_TIMEZONE`) replies with `build_monthly_embed`, publicly, with `AllowedMentions.none()`
- [ ] `/myactivity` with optional `user` (default the caller) and optional `month` replies with `build_profile_embed`, publicly
- [ ] An invalid or future month, or a user with no data, gets a clear short error reply (ephemeral) instead of a crash; the generic error reply is used for unexpected failures (same style as `cs2.py`)
- [ ] A shared `UserCooldown` (per user, 10 seconds) guards both commands, refunded on error, using the `_run_with_cooldown` pattern from `cogs/cs2.py`
- [ ] Command logic lives in plain methods and pure functions; the slash callbacks are thin wrappers; every command and parameter has a non-empty Discord `description=`
- [ ] `help/content.py`: new `activity` group in `GROUPS` and `CommandHelp` entries for both commands (summary at most 100 characters, details at most 600, examples starting with `/activity` or `/myactivity`); `GETTING_STARTED` mentions the new commands; `tests/test_help_content.py` updated (`len(keys)`, `EXPECTED_NAMES`)
- [ ] Tests with a fake store and `make_interaction()`: both commands happy path, bad month, future month, cooldown hit and refund, empty month, user with no data
- [ ] Docs: `README.md` commands table (checked by `tests/test_help_drift.py`) and a feature section covering parameters, accepted month format, cooldown, reply visibility, errors and caveats; intro "does three things" updated to include activity
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-009: Monthly auto-post
**Description:** As a member, I want the previous month's overview posted automatically at the start of each month, exactly once.

**Acceptance Criteria:**
- [ ] `activity/reporter.py` (no `discord` import): `MonthlyReporter(store, tz, clock)` with `due_month() -> Month | None` returning the previous month when it has not been posted (`reports_posted` says no) and the month has ended in `ACTIVITY_TIMEZONE`; at most one month per call, oldest unposted first, never older than the first recorded session's month, and never a month with no data
- [ ] `confirm(month)` marks the month posted; called after each send attempt (also on failure that cannot be retried, e.g. missing permissions logged once), so a broken channel cannot spam retries every hour
- [ ] An hourly `tasks.loop` in the cog runs one cycle: `due_month` → build report → post → `confirm`. It never raises (`try/except Exception: log.exception`), `before_loop` waits for ready, and it only starts when `ACTIVITY_CHANNEL_ID` is set
- [ ] The channel is resolved like `cogs/cs2.py` (`get_channel`, then `fetch_channel`; `NotFound`/`Forbidden` → missing) and checked with `cs2/channels.py` `channel_problem` / `is_post_channel`; the cog logs its own warning text (once per cause), not the CS2 text
- [ ] Tests with a fake clock: month not yet over, due after midnight local time, already posted, restart between post and confirm window, two missed months post oldest first, empty month skipped, channel missing/forbidden logs once, loop only starts with a channel, a failing send is confirmed and does not repeat
- [ ] Docs: `README.md` auto-post section (when it posts, timezone, one post per month, required channel permissions), `docs/deployment.md` Permissions (Send Messages, Embed Links in the channel), `docs/manual-checks.md` (how to force a post with a past month on the dev bot)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-010: Toasty message capture and parser (gated on fixtures)
**Description:** As a member, I want my `;pokemon` commands with Toasty recorded as catches.

**Blocked until:** the owner has supplied 5–10 real Toasty replies (a catch, a shiny, a rare, a failure/cooldown, an edited reply if Toasty edits) as files under `tests/fixtures/toasty/`. Do not invent reply formats. If the fixtures are missing, the story is skipped and noted in `progress.txt`.

**Acceptance Criteria:**
- [ ] `activity/pokemon.py` (no `discord` import): `parse_reply(content: str, embeds: Sequence[Mapping[str, Any]]) -> Catch | None` returns `Catch(pokemon, rarity, shiny)` for a successful catch and `None` for anything else, based only on the fixtures
- [ ] A new `catches` table `(message_id PRIMARY KEY, user_id, ts, pokemon, rarity, shiny)` (final columns follow the fixtures); store methods `record_catch`, `catches_between(start_ts, end_ts)`; duplicates by `message_id` are ignored
- [ ] Cog `on_message`: a message from a human whose content starts with `;pokemon` (case-insensitive) is remembered in a small TTL dict keyed by message id (capped size); a message authored by `TOASTY_BOT_ID` whose `reference.message_id` is in the dict is parsed and recorded for the original author; `on_message_edit` re-parses if the fixtures show Toasty edits replies
- [ ] Nothing is registered or recorded when `TOASTY_BOT_ID` is unset; no message content is stored beyond the parsed fields
- [ ] Tests: parser against every fixture, pairing logic (reply to remembered message, reply to unknown message, other bot, TTL expiry, duplicate), cog with fakes
- [ ] Docs: `README.md` (Toasty section, Message Content intent requirement, known limitation: tracking only works while the bot is online), `docs/deployment.md` (enable the intent in the Developer Portal), `docs/manual-checks.md` (`;pokemon` end to end)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-011: Catch stats in the overview
**Description:** As a member, I want catches in the monthly overview and my profile.

**Acceptance Criteria:**
- [ ] `activity/stats.py`: `catch_stats(catches, month, tz)` returns catches per user, shiny count, and the rarest catch of the month, using pure functions only
- [ ] `build_monthly_embed` shows a **Pokémon** field (top catchers with shared ranks, shinies, rarest catch) and `build_profile_embed` shows the user's catches, only when the month has catch data; `/activity` and the monthly post include it
- [ ] Field and embed limits from US-007 still hold with catch data
- [ ] Tests: ranking with ties, no catches, shiny and rarest, a long Pokémon name
- [ ] Docs: `README.md` activity section (catch stats), `help/content.py` details for `/activity` and `/myactivity` updated to mention Pokémon, `docs/manual-checks.md`
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-012: Final docs pass
**Description:** As the owner, I want every doc to match the final code.

**Acceptance Criteria:**
- [ ] `README.md`: layout block lists `activity/` and `cogs/activity.py`; Known limitations updated (voice only, text not counted, no history before the feature was enabled, Toasty catches missed while offline, month boundaries use `ACTIVITY_TIMEZONE`); every number, format and message taken from the code, not from this PRD
- [ ] `CLAUDE.md` Architecture list gets the new cog and `activity/` modules, and the intent rule mentions the conditional Message Content intent
- [ ] `docs/manual-checks.md` has complete "Activity tracking", "Monthly post" and "Toasty" checklists
- [ ] `docs/deployment.md` settings reference, intents/permissions and backup notes are consistent with the code; the old "no privileged intents" wording is gone (grep)
- [ ] Relative README links and anchors still resolve; `tests/test_help_drift.py` passes
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

## Functional Requirements

- FR-1: The bot records, for each non-bot member, sessions of connection to non-AFK voice channels with start and end timestamps.
- FR-2: A move between non-AFK channels ends one session and starts another; joining the AFK channel ends the session.
- FR-3: Open sessions are heartbeated every 60 seconds. On startup, sessions left open are closed at their last heartbeat, then current voice members get fresh sessions.
- FR-4: Months are calendar months in `ACTIVITY_TIMEZONE` (default UTC). Sessions are clipped to the window; no sessions are split in storage.
- FR-5: A day is "active" for a member with at least 5 minutes of voice time that local day.
- FR-6: Ranks are shared on ties (1, 1, 3). The shame list includes every member ever seen, including those with 0 hours in the month.
- FR-7: `/activity [month]` and `/myactivity [user] [month]` reply publicly with `AllowedMentions.none()`, are rate-limited per user, and report invalid input clearly.
- FR-8: When `ACTIVITY_CHANNEL_ID` is set, the previous month's overview is posted once per month, survives restarts, and does not retry endlessly when the channel is broken.
- FR-9: Toasty tracking runs only when `TOASTY_BOT_ID` is set, and the Message Content intent is requested only then.
- FR-10: A Toasty reply to a remembered `;pokemon` message is parsed and recorded as a catch for the original author. Only parsed fields are stored, never message text.
- FR-11: Embeds respect Discord limits (field values 1024, title 256, total 6000, 25 fields) with `+N more` truncation.
- FR-12: Docs listed per story are updated in the same story as the code.

## Non-Goals

- No counting of text messages, reactions, streaming or camera time.
- No history before the feature is enabled (Discord offers no voice history).
- No opt-out/privacy commands in v1 (private server of friends).
- No per-channel breakdowns or charts/images; text embeds only.
- No Toasty API calls and no Pokémon data source other than parsing Toasty's replies.
- No role assignment, rewards or punishments based on hours.
- No change to deployment/prod configuration beyond documenting new settings.

## Technical Considerations

- Follow existing patterns: `cs2/store.py` (SQLite + `asyncio.to_thread`), `cogs/cs2.py` (thin cog, `tasks.loop`, channel checks, `UserCooldown`), `cs2/channels.py` (reusable as-is), `cs2/formatting.py` `escape_name`, `cs2/embed.py` packing helper (made public in US-007).
- Clock injection (`Callable[[], float]`) everywhere time matters; tests use fixed 2026 dates and a `FakeClock`.
- All new config fields must have defaults. The drift-test fixture builds a real bot with default config and loads all extensions offline, so the cog's `setup`, `cog_load` and loops must be harmless without a channel or network.
- Voice tracking needs only the `voice_states` intent, which `Intents.default()` already includes. The members intent is not requested; names resolve through mentions and the existing member lookup patterns.
- `CLAUDE.md` workflow applies: branch `ralph/activity-tracking`, one commit per story, `./scripts/check.sh` passing, docs in the same commit.

## Success Metrics

- `./scripts/check.sh` passes after every story.
- Manual check in the dev server: a voice session is recorded, survives a bot restart, and shows up correctly in `/activity` and `/myactivity`.
- The monthly post appears once for a forced past month and never twice across restarts.
- With `TOASTY_BOT_ID` unset the bot starts without the Message Content intent; with it set and the intent enabled, `;pokemon` catches are counted.

## Open Questions

- What do Toasty's replies look like (embed vs plain text, does it edit its replies, how are shiny/rare shown)? US-010 and US-011 depend on real samples.
- Is 5 minutes the right threshold for an "active day"?
- Should the monthly post mention (ping) the shame-list members? Current design: no pings, `AllowedMentions.none()`.
- Which timezone should production use (default `UTC`)?
- Should members be able to opt out of tracking? Deferred (see Non-Goals).
