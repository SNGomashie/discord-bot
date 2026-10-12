# PRD: Session polls (tabletop date picker)

## Introduction

The server plays D&D and other tabletop RPGs. Finding a date that works for everyone happens today in chat ("who's free Friday?"), and it drags on because people reply late, forget, or are free on different days. The bot has no scheduling feature.

This PRD adds a **date picker that iterates in rounds until a slot works for everyone who wants to play**, plus reminders and nudges:

- `/session_poll` posts a **persistent poll message** with candidate slots generated from weekdays, times and a number of weeks.
- Players click **Join**, then **Vote**: an ephemeral view where they mark slots as "Works for me" or "Only if needed" (everything else is "No").
- When every joined player has voted, or the GM closes the round, or the optional round deadline passes, the bot **evaluates the round**:
  - if a slot fits, the bot shows the top 3 with **Confirm** buttons for the GM;
  - if none fits, the bot **opens the next round** on the following weeks (same days and times) and pings only the players who still need to vote, up to 6 rounds.
- A confirmed session gets an announcement with Discord timestamps (`<t:unix:F>` / `<t:unix:R>`, shown in each viewer's own time zone), **reminders** 24 h and 1 h before, and **nudges** (in-channel pings) for players who haven't voted.
- `/sessions` lists open polls and upcoming sessions; `/session_cancel` cancels one.

Saved availability profiles that pre-fill votes are a **separate PRD** (`prd-session-availability.md`). This PRD only adds a no-op `Prefiller` hook so that PRD is purely additive.

Design decisions (settled with the owner during planning):

- One server time zone for entering slots (`SESSIONS_TIMEZONE`, default = `ACTIVITY_TIMEZONE`). Display uses Discord timestamps.
- Everyone who clicks **Join** (plus the GM, who joins automatically) counts as "wants to participate". `min_players` lets a poll succeed with fewer than all of them.
- Reminders and nudges are **in-channel pings**, not DMs (no "DMs closed" failure mode). This is the first feature that pings: it sends `AllowedMentions(users=[...])` for exactly the users it means to ping. Everything else in the bot keeps `AllowedMentions.none()`.
- Commands are flat (`/session_poll`, `/sessions`, `/session_cancel`), not an `app_commands.Group`, because `/help` and `tests/test_help_drift.py` only see top-level commands.
- No new gateway intents and no new env var for a channel (reminders go to the channel the poll was posted in).

## Goals

- A GM can open a poll in one command and a group of friends can answer it in a few clicks, on a phone.
- A poll that does not fit in the first window keeps going by itself (next round) until a slot fits or 6 rounds are used, without the GM re-creating it.
- Polls, votes, sessions, reminders and nudges survive a bot restart (buttons keep working; nothing is sent twice; nothing is lost).
- All decisions (parsing, slot generation, scoring, round evaluation, due reminders/nudges, permissions) live in pure functions or a discord-free service class and are unit-tested, including DST edges. Cogs and views are thin.
- Every constant and message in the docs is read from the code, and `./scripts/check.sh` (including `tests/test_help_drift.py`) passes after every story.

## User Stories

### US-001: `SESSIONS_TIMEZONE` setting
**Description:** As the owner, I want a configurable time zone for session slots so that "19:00" means the same thing for everyone in the group.

**Acceptance Criteria:**
- [ ] `Config` gets `sessions_timezone: str`; `load_config` reads `SESSIONS_TIMEZONE` with the existing `_parse_timezone`; when unset or blank it takes the value of `activity_timezone` (so the default stays `UTC` when neither is set)
- [ ] An invalid IANA name raises `ConfigError` naming `SESSIONS_TIMEZONE` (same wording style as `ACTIVITY_TIMEZONE`)
- [ ] `tests/test_config.py`: unset falls back to `ACTIVITY_TIMEZONE`, explicit value wins, invalid value rejected, blank treated as unset
- [ ] Docs updated: `README.md` Configuration table, `.env.example` (commented, with a one-line comment), `docs/deployment.md` settings reference
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes (ruff, ruff format, mypy strict, pytest)

### US-002: Parse weekdays and times
**Description:** As a GM, I want to type `fri,sat` and `19:00,14:00` so that creating a poll is quick.

**Acceptance Criteria:**
- [ ] New package `src/ninja_bot/sessions/` (with `__init__.py`); `slots.py` imports nothing from `discord`
- [ ] `InvalidSessionInput(ValueError)` with a user-facing `.message` attribute (same shape as `InvalidMonthError`); all parse errors use it, never a bare `ValueError`
- [ ] `parse_days(text) -> tuple[int, ...]` returns weekday numbers (Mon=0) sorted and de-duplicated; accepts comma-separated three-letter or full English names, case-insensitive, with spaces around commas, plus the aliases `weekend` (sat, sun) and `weekdays` (mon-fri); empty, unknown names, or more than `MAX_DAYS_LENGTH` (64) characters raise `InvalidSessionInput` (the length check happens before any splitting)
- [ ] `parse_times(text) -> tuple[time, ...]` returns naive `datetime.time` values sorted and de-duplicated; accepts comma-separated 24-hour `H:MM` or `HH:MM` (`9:00`, `19:30`); uses an anchored `re.ASCII` regex and a `MAX_TIMES_LENGTH` (64) cap checked before conversion (same hardening as `media/clip.py::parse_timestamp`); `24:00`, `19:60`, `7pm`, empty input raise `InvalidSessionInput`
- [ ] Tests (`tests/test_session_slots.py`): each accepted form, aliases, case/space tolerance, de-duplication and ordering, every rejection above, non-ASCII digits (e.g. Arabic-Indic) rejected, over-length input rejected
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-003: Generate candidate slots
**Description:** As a GM, I want the bot to turn days, times and a number of weeks into concrete candidate dates so that players pick from real options.

**Acceptance Criteria:**
- [ ] `slots.py` defines `MAX_SLOTS = 25`, `MAX_WEEKS = 8`, `DEFAULT_WEEKS = 2`, `DEFAULT_DURATION_HOURS = 4`, a frozen `Slot(start: float, end: float)` (UTC Unix seconds, `end = start + duration`), and `parse_start_date(text, tz, now) -> date` (blank = tomorrow in `tz`; `YYYY-MM-DD`, ASCII only; a date before today raises `InvalidSessionInput`)
- [ ] `generate_slots(start_date, weeks, days, times, tz, duration_hours, now) -> list[Slot]` returns the slots in chronological order for the window `[start_date, start_date + weeks * 7 days)`, built from local wall-clock `datetime(y, m, d, h, mi, tzinfo=tz)`
- [ ] Slots whose start is not after `now` are dropped; an empty result raises `InvalidSessionInput` ("no slots in that window")
- [ ] More than `MAX_SLOTS` raises `InvalidSessionInput` with a message that says how many were generated and suggests fewer weeks, days or times
- [ ] DST: a local time that does not exist (spring-forward gap, e.g. 02:30 on 2026-03-29 in `Europe/Amsterdam`) is skipped, not shifted; a repeated time (fall-back fold) yields one slot using the first occurrence (`fold=0`); both behaviours are documented in a docstring
- [ ] `next_window_start(previous_start: date, weeks: int) -> date` returns the first day after the previous window (used for later rounds)
- [ ] Tests with `Europe/Amsterdam` around 2026-03-29 and 2026-10-25, a window crossing a month boundary, `now` partway through the first day, the 25-slot cap (exactly 25 allowed, 26 rejected), `MAX_WEEKS` handled by the caller (see US-011), and the start-date rules
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-004: Score votes and evaluate a round
**Description:** As a GM, I want the bot to decide whether a round found a date and which dates are best so that nobody has to count votes by hand.

**Acceptance Criteria:**
- [ ] New `sessions/scoring.py` (no `discord` import): `Vote` enum (`YES`, `IF_NEEDED`, `NO`), frozen `SlotScore(slot_id, yes, if_needed, no, missing, attending)` where `attending = yes + if_needed`
- [ ] `score_slots(slot_ids, players, votes) -> list[SlotScore]` where `players` are the joined user ids and `votes` is a mapping `(user_id, slot_id) -> Vote`; a player with no recorded vote on a slot counts as `missing` for that slot; votes from users who are not in `players` are ignored
- [ ] `evaluate_round(scores, players, gm_id, votes, min_players, round_number, max_rounds) -> Fits | NextRound | NoFit` (frozen dataclasses): `required = min_players or len(players)`; a slot *fits* when `attending >= required` **and** the GM's own vote on it is `YES` or `IF_NEEDED`; `Fits(top)` carries up to 3 fitting scores ranked by `yes` descending, then `if_needed` ascending, then earliest slot id; no fitting slot and `round_number < max_rounds` gives `NextRound`; no fitting slot at `max_rounds` gives `NoFit`
- [ ] `MAX_ROUNDS = 6`; `MAX_PLAYERS = 25`
- [ ] `pending_players(players, round_slot_ids, votes) -> list[int]`: joined players with no recorded vote on any slot of the round (a player who voted "none of these" has recorded `NO` votes and is not pending)
- [ ] `all_voted(players, round_slot_ids, votes) -> bool` is true when `pending_players` is empty and there is at least one player
- [ ] Tests: unanimous yes; mix of yes and if-needed ranking and tie-break; `min_players` lower than the joined count; GM votes No on the otherwise best slot; no fit with rounds left; no fit at the last round; stranger votes ignored; no players; "none of these" is not pending
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-005: Store: polls, rounds, slots, players
**Description:** As a developer, I need durable storage for polls so that restarts do not lose them.

**Acceptance Criteria:**
- [ ] New `sessions/store.py` defining frozen dataclasses `Poll(id, guild_id, channel_id, message_id, name, gm_id, days, times, weeks, duration_hours, min_players, round_hours, round_number, window_start, status, created_at, round_started_at)` and `PollSlot(id, poll_id, round_number, start, end)`; `days` and `times` are stored as comma-separated strings and mapped back by the row mapper; `status` is one of `voting`, `awaiting_confirm`, `confirmed`, `failed`, `cancelled` (constants)
- [ ] `SessionStore` Protocol (async methods, docstrings) and `SqliteSessionStore(path)` implementing it, in `<DATA_DIR>/sessions.sqlite3`, following `activity/store.py`: `_SCHEMA` tuple of `CREATE ... IF NOT EXISTS`, lazy schema on first connect, sync `_name` methods under `with closing(self._connect()) as conn, conn:`, public async wrappers via `asyncio.to_thread`
- [ ] Methods in this story: `create_poll(...) -> Poll` (inserts the poll and its round-1 slots, auto-joins the GM, in one transaction), `get_poll(poll_id)`, `set_poll_message(poll_id, message_id)`, `poll_slots(poll_id, round_number)`, `join(poll_id, user_id) -> bool` (False when already joined or the poll already has `MAX_PLAYERS` players), `leave(poll_id, user_id)` (also deletes that user's votes for the poll), `players(poll_id)`, `open_polls(guild_id)` (statuses `voting` and `awaiting_confirm`, oldest first), `set_status(poll_id, status)`
- [ ] Tests (`tests/test_session_store.py`, real SQLite in `tmp_path`): create/get round trip, slots ordering, join twice returns False, join beyond `MAX_PLAYERS`, leave removes votes (after US-006 lands; add that assertion there), GM auto-joined, a second store on the same path sees the data (restart test), Protocol satisfaction
- [ ] Docs updated: `README.md` Data and storage (new `sessions.sqlite3` table) and `docs/deployment.md` backup notes
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-006: Store: votes, rounds, sessions, reminders, nudges
**Description:** As a developer, I need votes, confirmed sessions and sent-markers stored so that evaluation, reminders and nudges are restart-safe.

**Acceptance Criteria:**
- [ ] `Session(id, poll_id, guild_id, channel_id, message_id, name, gm_id, start, end, roster, cancelled)` frozen dataclass (`roster` is a tuple of user ids)
- [ ] Store methods: `record_votes(poll_id, user_id, votes: Mapping[int, Vote])` (replaces that user's votes for the given slots atomically), `votes(poll_id, round_number) -> dict[tuple[int, int], Vote]`, `advance_round(poll_id, slots: Sequence[Slot], window_start, message_id=None) -> Poll` (increments `round_number`, sets `round_started_at`, inserts slots, status back to `voting`, clears the nudge counter), `confirm(poll_id, slot_id, roster) -> Session` (sets status `confirmed`, inserts the session), `cancel_poll(poll_id)`, `get_session(session_id)`, `upcoming_sessions(guild_id, now)`, `active_sessions(now)` (uncancelled, `end` in the future), `cancel_session(session_id)`, `mark_reminder(session_id, offset_seconds)` (idempotent, `INSERT OR IGNORE`), `reminders_sent(session_id) -> set[int]`, `nudge_count(poll_id, round_number)`, `record_nudge(poll_id, round_number, at)`, `last_nudge(poll_id, round_number) -> float | None`
- [ ] Votes of earlier rounds stay stored but only the current round's slots are used for scoring
- [ ] Tests: replace-on-revote, votes limited to the round, `advance_round` increments and resets nudges, `confirm` stores the roster and flips status, `cancel_session` removes it from `upcoming_sessions` and `active_sessions`, reminder marker idempotent, nudge counters per round, restart persistence of every table
- [ ] Add the "leave removes votes" assertion deferred from US-005
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-007: Service: create a poll, join/leave, vote
**Description:** As a developer, I want the poll rules in one discord-free class so that button handlers stay trivial.

**Acceptance Criteria:**
- [ ] New `sessions/service.py` (no `discord` import): `SessionService(store, tz, clock, prefiller)`; `Prefiller` is a Protocol `async prefill(user_id, slots) -> dict[int, Vote]`, with `NoPrefill` returning `{}`; the service applies prefill votes when a user joins (none in this PRD) and never overwrites an explicit vote
- [ ] `create_poll(guild_id, channel_id, gm_id, name, days_text, times_text, weeks, start_text, duration_hours, min_players, round_hours) -> Poll` parses with the US-002/US-003 functions, validates `weeks` in `1..MAX_WEEKS`, `name` non-blank and at most `MAX_NAME_LENGTH` (60) characters, `min_players >= 2` when given, `round_hours` in `1..336` when given, and raises `InvalidSessionInput` for everything else; it limits each guild to `MAX_OPEN_POLLS` (5) open polls
- [ ] `join`, `leave` return a small result enum (`JOINED`, `ALREADY_JOINED`, `FULL`, `LEFT`, `NOT_JOINED`, `POLL_CLOSED`); the GM cannot leave their own poll (`GM_CANNOT_LEAVE`)
- [ ] `submit_vote(poll_id, user_id, yes_ids, maybe_ids) -> VoteResult`: the user must have joined and the poll must be `voting`; ids must belong to the current round's slots; a slot id in both lists counts as `YES`; slots in neither are recorded as `NO`; "none of these" is `submit_vote(..., (), ())`
- [ ] User-facing refusals are constants in `sessions/content.py`, never inline strings in handlers
- [ ] Tests with a fake store and fake clock: every validation error, the open-poll cap, join/leave results, vote on a closed poll, vote by a non-member, vote with a foreign slot id, double submission replaces
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-008: Service: close a round, advance, confirm, cancel
**Description:** As a GM, I want the poll to decide what happens after a round so that it keeps going until a date is found.

**Acceptance Criteria:**
- [ ] `SessionService.close_round(poll_id, by_user_id, is_manager, reason) -> RoundOutcome`: only the GM or a manager may close manually (`reason == "manual"`); automatic calls (`"all_voted"`, `"deadline"`) skip the permission check; it loads players, slots and votes, runs `evaluate_round`, and for `Fits` sets status `awaiting_confirm`, for `NextRound` generates the next window with `next_window_start` + `generate_slots` (same days, times, weeks) and calls `store.advance_round`, for `NoFit` sets status `failed`
- [ ] If the next window yields no slots (all in the past is impossible by construction, but the 25-slot cap or DST gaps may leave none) the outcome is `NoFit` with a distinct reason
- [ ] `RoundOutcome` is a frozen union carrying what the cog must render: the poll, the ranked fitting scores or the new slots, and the pending players
- [ ] A round already evaluated is not evaluated twice: calling `close_round` when the poll is not in `voting` returns `NotVoting` (idempotent under double clicks and a racing loop)
- [ ] `confirm(poll_id, slot_id, by_user_id, is_manager) -> Session`: only the GM or a manager; the poll must be `awaiting_confirm` and the slot must be one of the offered top slots; the roster is the joined players whose vote on that slot is `YES` or `IF_NEEDED`
- [ ] `cancel_poll`, `cancel_session` (GM or manager only; refuse unknown/finished ids with a content message)
- [ ] `should_auto_close(poll, players, slot_ids, votes, now) -> str | None` returns `"all_voted"`, `"deadline"` (when `round_hours` is set and `round_started_at + round_hours * 3600 <= now`), or `None`
- [ ] Tests: fit, next round, last round no-fit, double close, non-GM close refused, manager allowed, confirm builds the right roster, confirm with a slot that was not offered refused, cancel permissions, auto-close reasons
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-009: Pure reminder and nudge schedule
**Description:** As a developer, I want the "what is due now" decisions in pure functions so that reminders and nudges are testable without Discord or a clock.

**Acceptance Criteria:**
- [ ] New `sessions/reminders.py` (no `discord` import): `REMINDER_OFFSETS = (86_400, 3_600)` seconds before start, `NUDGE_INTERVAL_SECONDS = 86_400`, `MAX_NUDGES = 3`
- [ ] `due_reminder(session, now, sent) -> int | None`: the offset to post now, or `None`. A reminder is due when `start - offset <= now < start` and its offset is not in `sent`. If several are due (e.g. the bot was down), only the **smallest** offset (closest to the start) is returned; `skipped_offsets(session, now, sent)` returns the earlier ones to mark as sent without posting. Nothing is due once `now >= start`.
- [ ] `due_nudge(poll, pending, nudge_count, last_nudge, now) -> bool`: true when the poll is `voting`, `pending` is non-empty, fewer than `MAX_NUDGES` nudges were sent this round, and `now >= (last_nudge or round_started_at) + NUDGE_INTERVAL_SECONDS`
- [ ] A `SessionNotifier(store, clock)` class with `due_reminders() -> list[tuple[Session, int]]` and `due_nudges() -> list[tuple[Poll, list[int]]]` (reads the store) and `confirm_reminder(session_id, offset)` / `confirm_nudge(poll_id, round_number)` that the cog calls **after every send attempt** (same contract as `MonthlyReporter.confirm`: a failed send is not retried forever)
- [ ] Tests: 24 h and 1 h boundaries (exactly at the boundary, one second before), bot down across the 24 h point, never after the start, nudge spacing and cap, nudge only while `voting`, restart persistence through the notifier with a real store
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-010: Embeds and formatting
**Description:** As a player, I want clear poll, result and session messages so that I can decide quickly on a phone.

**Acceptance Criteria:**
- [ ] New `sessions/content.py` (all user-facing strings as named constants, in the same spirit as `help/content.py`) and `sessions/embed.py`
- [ ] `build_poll_embed(poll, slots, scores, players, pending, names)`: title is the poll name (escaped with `cs2.formatting.escape_name`), a round indicator (`Round 2 of 6`), a description with GM, duration and `min_players` when set, one line per slot with `<t:unix:F>` and the counts (`✅ yes · 🤔 if needed`), a `Joined` field and a `Waiting for` field using `<@id>` mentions packed with `cs2.formatting.pack_lines`, a deadline line (`<t:unix:R>`) when `round_hours` is set, and the footer text from content; stays within Discord embed limits (25 slots, 25 players) and is verified against `len(embed)`
- [ ] `build_result_embed(poll, fits)` lists the top 3 slots with their yes/if-needed counts and who can't make it; `build_next_round_embed(poll, slots, pending)` and `build_no_fit_embed(poll)` (the latter suggests fewer days, more weeks or lower `min_players`)
- [ ] `build_session_embed(session)` (name, `<t:unix:F>`, `<t:unix:R>`, GM, roster mentions) and `build_list_embed(polls, sessions)` (used by `/sessions`; empty state text from content)
- [ ] Functions are pure and take plain data (no store, no `discord.Member`); mentions never inside code blocks
- [ ] Tests: required fields present, `<t:` timestamps are the slot starts, limits respected at max slots/players, escaped name, empty list state, pending list truncation uses `+N more`
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-011: `/session_poll` command and Join/Leave buttons
**Description:** As a GM, I want to open a poll with one command and let players join with a button.

**Acceptance Criteria:**
- [ ] New cog `cogs/sessions.py` (`Sessions`, `async def setup(bot)` building `SqliteSessionStore(Path(bot.config.data_dir) / "sessions.sqlite3")`, `SessionService(..., NoPrefill())`, clock `time.time`); registered in `EXTENSIONS` in `bot.py`; `cog_app_command_error` fallback like the other cogs
- [ ] `/session_poll` is `guild_only`, with parameters and Discord descriptions: `name` (`Range[str, 1, 60]`), `days` (e.g. "fri,sat"), `times` (e.g. "19:00,14:00"), `weeks` (`Range[int, 1, 8]`, default 2), `start` (`YYYY-MM-DD`, default tomorrow), `duration_hours` (`Range[int, 1, 12]`, default 4), `min_players` (`Range[int, 2, 25]`, optional), `round_hours` (`Range[int, 1, 336]`, optional deadline per round)
- [ ] `handle_session_poll(interaction, ...)` calls `SessionService.create_poll`, checks the channel with `channel_problem`/`is_post_channel` (an error reply when the bot cannot post there), posts the poll embed with the buttons via `interaction.response.send_message` (public, `AllowedMentions.none()`), stores the message id with `set_poll_message`; input errors reply ephemeral with `exc.message`; unexpected errors are logged and replied with a generic ephemeral message
- [ ] `sessions/views.py` (the only module importing `discord.ui`): a `DynamicItem[Button]` subclass with `custom_id` template `ses:(?P<poll>\d+):(?P<action>[a-z]+)(?::(?P<arg>\d+))?` registered with `bot.add_dynamic_items(...)` in `cog_load`; this story implements the **Join** and **Leave** actions, each re-rendering the poll embed via `interaction.response.edit_message`
- [ ] The action callbacks only call `SessionService` and render results (`handle_*` methods are directly callable with `MagicMock` interactions in tests)
- [ ] Per-user `UserCooldown` (30 s) on `/session_poll`, refunded when the poll was not posted; the cooldown reply is ephemeral
- [ ] `help/content.py`: new `SESSIONS` group and a `CommandHelp` for `session_poll` (parameters, formats, examples, notes), plus a Getting started line; `README.md` commands table row and a new "Session polls" feature section (parameters, accepted formats, cooldown/refund rules, reply visibility, errors); `README.md` Contents list; `CLAUDE.md` Architecture entries for `sessions/` and `cogs/sessions.py`; the README layout block
- [ ] Tests: handle with valid input posts the embed and stores the message id; every `InvalidSessionInput` replied ephemerally; cooldown and refund; join/leave button handlers; dynamic item `from_custom_id` parsing round trip; `tests/test_help_drift.py` passes (the cog loads with the minimal `Config`)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-012: Vote view and live poll refresh
**Description:** As a player, I want to mark which slots work for me privately and see the poll update live.

**Acceptance Criteria:**
- [ ] The **Vote** button replies with an ephemeral view (`discord.ui.View`, timeout 10 minutes) with two multi-selects over the current round's slots (labels like `Fri 17 Oct 19:00` in `SESSIONS_TIMEZONE`, because select labels cannot hold Discord timestamps): **Works for me** and **Only if needed**, pre-selected with the user's existing votes; a **None of these work** button; a **Save** button
- [ ] A user who has not joined is told to press Join first (ephemeral); a closed poll replies that voting is over
- [ ] Save calls `SessionService.submit_vote`, then re-renders the poll message embed (edit the message by `poll.message_id`; failures are logged and do not break the vote) and confirms ephemerally
- [ ] After a vote the cog checks `should_auto_close(...)`; on `"all_voted"` it calls the round-closing path from US-013 (this story may stub it behind a method `advance_if_ready(poll_id)` with a test double)
- [ ] The slot labels function is pure and unit-tested (`format_slot_label(slot, tz)`), including the 100-character label limit and tz conversion
- [ ] Tests: existing votes pre-selected, select overlap resolves to YES, none-of-these records all NO, non-member refused, closed poll refused
- [ ] `docs/manual-checks.md`: new `## Session polls` section with the vote-view checks (ephemeral, phone layout, pre-selection)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-013: Round close, auto-advance and Confirm
**Description:** As a GM, I want the poll to evaluate itself and let me confirm the winning date.

**Acceptance Criteria:**
- [ ] The cog method `advance_if_ready(poll_id)` and the **Close round** button (GM or `manage_events` only; others get an ephemeral refusal) call `SessionService.close_round` and render its `RoundOutcome`:
  - `Fits`: edit the old poll message (buttons removed, "Round closed"), post the result embed with up to 3 **Confirm** buttons (`custom_id` `ses:<poll>:confirm:<slot_id>`) and mention the GM
  - `NextRound`: edit the old message as closed, post a new poll message for the new round (store its id with `set_poll_message`), and ping only the pending players (`AllowedMentions(users=[pending ids])`)
  - `NoFit`: post the no-fit embed, buttons removed
- [ ] The **Confirm** action calls `SessionService.confirm` (GM or manager only), edits the result message to say which slot was chosen, and posts the session embed announcing the roster with `AllowedMentions(users=[roster ids])`
- [ ] The cog extends the dynamic item with the `close` and `confirm` actions; stale buttons (poll in another status, unknown poll id) reply ephemerally instead of raising
- [ ] All sends go through `send_safely`; a failed edit of an old message is logged and does not abort the flow
- [ ] Tests (cog `handle_*` with `MagicMock` interactions and a fake service/store): each outcome posts the right messages with the right `allowed_mentions`; non-GM refused; double click on Close or Confirm is harmless; stale button
- [ ] `docs/manual-checks.md`: round advance and confirm flows (including a restart mid-poll)
- [ ] `README.md` Session polls section updated (rounds, fit rule, `min_players`, GM must attend, `MAX_ROUNDS`, who can close/confirm)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-014: Reminders, nudges and deadline loop
**Description:** As a player, I want to be reminded before a session and nudged when I forget to vote.

**Acceptance Criteria:**
- [ ] The cog starts a 60-second `tasks.loop` (`NOTIFY_CHECK_SECONDS`) in `cog_load`, cancelled in `cog_unload`, with `before_loop` awaiting `wait_until_ready()`; the body is `run_notify_cycle()`, which never raises (wraps everything, `log.exception`)
- [ ] Each cycle, in this order: (1) auto-close rounds where `should_auto_close` returns `"deadline"` or `"all_voted"` through the US-013 path; (2) `SessionNotifier.due_reminders()`: post a reminder embed to the session's channel with `AllowedMentions(users=[roster])` and mark the offset sent after the attempt, marking `skipped_offsets` without posting; (3) `due_nudges()`: post a short message that replies to the poll message and pings the pending players, then `confirm_nudge`
- [ ] The target channel is resolved with one `PostChannelResolver` per channel id (cached in a dict; warning template names the channel problem once per cause); a channel problem skips the post and warns once, a deleted channel does not crash the loop
- [ ] Sessions past their end are no longer processed; cancelled sessions and polls are never pinged
- [ ] Tests via `run_notify_cycle()` with fake clock/store/channel: 24 h and 1 h reminders posted once each, restart does not repeat, bot-down catch-up posts one reminder, nudge cadence and cap, deadline closes a round, cancelled session silent, `Forbidden` on send is logged and not retried forever, the loop never raises
- [ ] `docs/manual-checks.md`: reminders (shorten offsets via a documented `sqlite3` UPDATE of the session start), nudges, deadline
- [ ] `README.md` Session polls section updated (reminder offsets, nudge cadence and cap, in-channel pings, what happens when the channel is gone)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-015: `/sessions` and `/session_cancel`
**Description:** As a player, I want to see what is open and upcoming; as a GM, I want to cancel a poll or session.

**Acceptance Criteria:**
- [ ] `/sessions` (`guild_only`, no parameters): public embed from `build_list_embed` with open polls (name, round, link to the poll message, who is pending) and upcoming sessions (`<t:unix:F>`, `<t:unix:R>`, GM), `AllowedMentions.none()`, empty-state text; per-user `UserCooldown` (10 s) refunded unless posted
- [ ] `/session_cancel item` where `item` autocompletes the guild's open polls and upcoming sessions (value is `poll:<id>` or `session:<id>`, label is the name plus date; at most 25 choices, filtered by the typed text); the handler requires GM or `manage_events`, cancels via the service, edits the original poll message (buttons removed, "Cancelled") when it exists, and replies publicly with a short confirmation (`AllowedMentions.none()`); unknown or already-finished items get an ephemeral reply
- [ ] Both commands have Discord descriptions for the command and every parameter, `CommandHelp` entries in `help/content.py`, README commands-table rows and updated Session polls section; `tests/test_help_drift.py` passes
- [ ] Tests: list rendering from fake store data, autocomplete filtering/cap, permission refusal, cancel of a poll and of a session, cancelled items disappear from the list and from the notifier
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-016: Documentation pass
**Description:** As the owner, I want the docs to match the final code so that nothing is stale.

**Acceptance Criteria:**
- [ ] Re-read every constant and message from the code (`MAX_SLOTS`, `MAX_WEEKS`, `MAX_ROUNDS`, `MAX_PLAYERS`, `MAX_OPEN_POLLS`, cooldowns, reminder offsets, nudge interval/cap, the fit rule) and make `README.md` match: commands table, Session polls section, Configuration (`SESSIONS_TIMEZONE`), invite permissions (Send Messages, Embed Links, Read Message History in the poll channel; no new intents), Data and storage, Known limitations (polls are per-channel messages; departed members still counted/pinged; one server time zone; no conflict detection across polls; slot labels in selects use the server zone while embeds use Discord timestamps), layout block
- [ ] `docs/deployment.md`: settings reference and Permissions; `docs/manual-checks.md`: complete Session polls checklist (persistent buttons survive a restart, vote view on a phone, round advance, confirm, 24 h / 1 h reminders, nudges, cancel); `.env.example`; `CLAUDE.md` Architecture list complete (every new module with its responsibility)
- [ ] `grep` shows no stale placeholders or TODOs in the docs; README relative links and anchors resolve
- [ ] `CHANGELOG.md` is **not** edited here (written at PR time)
- [ ] `./scripts/check.sh` passes

## Functional Requirements

- FR-1: `SESSIONS_TIMEZONE` (IANA name) defaults to `ACTIVITY_TIMEZONE`; all slot entry and select labels use it.
- FR-2: `/session_poll` generates candidate slots from `days`, `times`, `weeks` (1-8, default 2) and `start` (default tomorrow); at most `MAX_SLOTS` (25) slots per round; slots already in the past are dropped; nonexistent DST times are skipped.
- FR-3: At most `MAX_OPEN_POLLS` (5) open polls per guild and `MAX_PLAYERS` (25) players per poll.
- FR-4: The GM is joined automatically and cannot leave; every other player joins and leaves with buttons.
- FR-5: A vote assigns each current-round slot `YES`, `IF_NEEDED` or `NO`; a slot not selected is `NO`; re-voting replaces the previous vote.
- FR-6: A round is evaluated when all joined players have voted, when the GM or a user with Manage Events presses **Close round**, or when `round_hours` (if set) elapses.
- FR-7: A slot fits when `yes + if_needed >= required` (`min_players`, else all joined players) and the GM voted `YES` or `IF_NEEDED` on it. At most 3 fitting slots are offered, ranked by `yes` descending, `if_needed` ascending, earliest first.
- FR-8: With no fitting slot the poll opens the next round (next window, same days/times) and pings only players who have not voted, up to `MAX_ROUNDS` (6); after the last round the poll ends as failed with advice.
- FR-9: Only the GM or a user with Manage Events can close a round, confirm a slot, or cancel; everyone can join, leave and vote.
- FR-10: Confirming creates a session whose roster is the joined players who can attend the chosen slot; it is announced with `<t:unix:F>` and `<t:unix:R>`.
- FR-11: A reminder is posted 24 h and 1 h before the start, to the poll's channel, pinging the roster; after downtime only the closest missed reminder is posted; nothing is posted once the session started.
- FR-12: A nudge pings the players who have not voted every 24 h of an open round, at most 3 times per round.
- FR-13: Only the pings in FR-8, FR-10, FR-11, FR-12 and the GM mention on a fit may mention users, and only the users meant; all other messages use `AllowedMentions.none()`.
- FR-14: Buttons keep working after a restart (dynamic items keyed by poll id); stale buttons reply ephemerally.
- FR-15: Failed sends and edits are logged and swallowed (`send_safely`); the notify loop never raises.
- FR-16: `/sessions` lists open polls and upcoming sessions; `/session_cancel` (autocomplete) cancels one and stops all its reminders and nudges.
- FR-17: All user-facing strings are constants in `sessions/content.py`; commands and parameters have Discord descriptions; `/help`, the README table and the commands stay in sync (`tests/test_help_drift.py`).

## Non-Goals (Out of Scope)

- Saved availability profiles and vote pre-filling (separate PRD `prd-session-availability.md`; only the no-op `Prefiller` hook is added here).
- Per-user time zones.
- Creating a native Discord Scheduled Event, campaigns, recurring sessions, attendance tracking, dice roller, session notes (follow-up ideas).
- DMs of any kind.
- Detecting conflicts between polls or sessions.
- Removing departed members from rosters.
- Editing a poll's days/times after creation (cancel and re-create).
- Any LLM call.

## Design Considerations

- Keep logic out of Discord handlers: parsing, generation, scoring, evaluation, the reminder/nudge schedule and permission rules are pure or live in `SessionService`/`SessionNotifier`; the cog and `views.py` only translate interactions to service calls and render results. (The approved plan listed no service class; it is added here because the button handlers would otherwise carry the rules.)
- Reuse: `UserCooldown` (`media/cooldown.py`), `send_safely` and `PostChannelResolver` (`posting.py`), `channel_problem` / `is_post_channel` (`cs2/channels.py`), `escape_name` and `pack_lines` (`cs2/formatting.py`), the `Month`/`split_local` conventions of `activity/periods.py` for tz handling, the injectable clock pattern, and the confirm-after-send contract of `MonthlyReporter`.
- Select-menu labels cannot render Discord timestamps, so they use a fixed local format in `SESSIONS_TIMEZONE`; embeds use `<t:unix:...>`.
- Discord limits: 25 options per select, 25 embed fields, 100-character labels, 5 buttons per row; the poll message uses one action row of Join / Vote / Leave / Close round.

## Technical Considerations

- discord.py 2.7.1: `discord.ui.DynamicItem` + `bot.add_dynamic_items`, no `bot.add_view` per poll. The persistent custom ids must be at most 100 characters.
- `intents.default()` already covers guilds and interactions; no privileged intents.
- Storage: new file `<DATA_DIR>/sessions.sqlite3` with the lazy-schema pattern; new tables only (`CREATE TABLE IF NOT EXISTS`), so no migration framework is needed.
- `UserCooldown` is in-memory (reset on restart), like the other cogs.
- Races (double click, loop and click evaluating the same round) are handled by the status check in `close_round` and the idempotent store writes.
- `tests/test_help_drift.py` loads every extension with a minimal `Config`; the cog must construct without a network or a channel setting.

## Success Metrics

- A poll with 5 players reaches a confirmed session using only buttons (manual check in the test server).
- A poll left alone advances through rounds and reminders without the GM touching the bot.
- `./scripts/check.sh` passes with the new tests after every story; no existing test changes except additions to `test_config.py` and the registration of the new extension.

## Open Questions

- Should a rejected "fits only if GM attends" rule be relaxed for GM-less games (e.g. a `gm_optional` flag)? Default in this PRD: the GM must attend.
- Should joined players who leave Discord be dropped from rosters automatically? Deferred (listed in Known limitations).
