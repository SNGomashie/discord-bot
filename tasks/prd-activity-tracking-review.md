# PRD: Activity tracking review follow-ups

## Introduction

`/code-review` (high) of the `ralph/activity-tracking` branch found correctness problems and cleanups in the activity feature (see `tasks/prd-activity-tracking.md`). The three most important were checked against the code before writing this PRD: the wall of shame draws from every user ever seen, the current streak resets to 0 until a member joins voice today, and the monthly report is marked posted even when building it failed.

This PRD fixes them on the same branch (`ralph/activity-tracking`), so the feature ships fixed. Each story is one finding. Where a finding changes what a user sees, the same story updates the docs.

## Goals

- The wall of shame only names people who are plausibly still around (current members with recent voice activity), and never shames someone for a month before they existed in the data.
- Streaks and awards are correct for a month that is still running.
- A transient error can never silently lose a month's automatic report.
- Obviously invalid months are rejected with the normal "use a month like 2026-09" reply.
- The reporter's hourly check stays cheap as months accumulate.
- No copy-pasted channel/posting helpers, duplicated interval splitting, dead parameters, or logic in the Discord handlers.

## User Stories

### US-001: Wall of shame only lists plausible members
**Description:** As a member, I want the wall of shame to list people who are actually part of the server's voice crowd, so that it isn't filled with long-gone members.

**Acceptance Criteria:**
- [ ] `known_users()` (store Protocol and `SqliteActivityStore`) is replaced or supplemented by a method returning, per user, the first and last voice session start (for example `voice_user_ranges() -> dict[int, tuple[float, float]]`); users who only appear in `pokemon_events` are not shame candidates
- [ ] New pure function in `activity/stats.py` (or `activity/report.py`), e.g. `shame_candidates(ranges, month_start_ts, month_end_ts)`, returns the users whose first session is before the end of the viewed month and whose last session is at most `SHAME_LOOKBACK_DAYS = 90` days before the end of the viewed month (a named constant); `build_monthly_report` uses it instead of the raw known-users set, and still includes users who have voice time that month
- [ ] A user whose first session is after the end of the viewed month is never shamed for it (viewing `2026-01` does not list someone first seen in September)
- [ ] Unit tests: user first seen after the month, user last seen 91 days before month end (excluded) and 89 days (included), Pokémon-only user excluded, user with 0h in range included, ordering and the `SHAME_LIMIT` unchanged
- [ ] Docs: `README.md` activity section says who can appear on the wall of shame (seen in voice within the last 90 days and already known by that month) and `docs/manual-checks.md` item adjusted if it mentions the shame list
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-002: Skip departed members in the wall of shame
**Description:** As a member, I want people who left the server not to appear as dead `<@id>` mentions on the wall of shame.

**Acceptance Criteria:**
- [ ] In the cog, shame candidates (not the leaderboard) are checked against the guild with the existing REST-backed lookup pattern from `cogs/cs2.py` (`member_lookup` / `MemberCache`: cache first, then `fetch_member`; `NotFound` means not a member); the lookup needs no members intent
- [ ] A candidate that is definitively not a member is dropped before `SHAME_LIMIT` is applied, so the list still fills up with eligible members; transient lookup errors keep the candidate (never drop on an error) and are not cached
- [ ] The filtering decision is a pure function over `(candidate ids, set of departed ids)`; the cog only gathers the departed set, for both `/activity` and the monthly post
- [ ] Tests: departed member dropped and list refilled, lookup error keeps the candidate, all candidates departed gives an empty shame field (no empty embed field), `/activity` and the monthly post both filter
- [ ] Docs: `README.md` activity section mentions that members who left the server are skipped
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-003: Current streak stays alive until the day ends
**Description:** As a member, I want my current streak to still count while I haven't joined voice yet today.

**Acceptance Criteria:**
- [ ] In `monthly_stats` (`activity/stats.py`), for the month that contains `now`, the current streak ends at today when today is an active day, otherwise at yesterday (day-of-month − 1); for ended months it still ends on the last day of the month; on the 1st with no activity yet today the current streak is 0
- [ ] `_run_ending_on` or its caller is the only place that decides this; `longest_streak` is unchanged
- [ ] Tests: 10 active days then a quiet morning on day 11 gives current streak 10; today active gives 11; yesterday inactive and today inactive gives 0; 1st of the month; ended month unchanged
- [ ] Docs: `README.md` activity section states that streaks are counted within the viewed month (they restart on the 1st) and that today doesn't break a running streak; `help/content.py` text for `/myactivity` is corrected if it claims otherwise
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-004: Climber and drop awards only for ended months
**Description:** As a member, I want "Biggest climber" and "Biggest drop" to compare complete months, so that mid-month results aren't nonsense.

**Acceptance Criteria:**
- [ ] `compute_awards` (`activity/awards.py`) omits Biggest climber and Biggest drop when the viewed month contains `now` (the month is not over); every other award is unchanged
- [ ] Tests: viewing the current month on day 3 yields neither award even when last month had data; viewing an ended month still yields them; a month with no previous data still yields neither
- [ ] Docs: `README.md` activity section says these two awards appear for completed months only
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-005: A failed report build is retried, not marked posted
**Description:** As a member, I want the monthly overview to be retried when it couldn't be built, instead of being lost forever.

**Acceptance Criteria:**
- [ ] In `_post_report` (`cogs/activity.py`) `reporter.confirm(month)` is no longer in a `finally` that also covers building the report; if building the report (`monthly_report`, embed) raises, the month is not confirmed, the error is logged with `log.exception`, and the next hourly cycle tries again
- [ ] The month is still confirmed after every send attempt, successful or not, so a broken channel or a Discord error on send can't repeat endlessly (existing behaviour)
- [ ] The cycle still never raises
- [ ] Tests: report build raises then succeeds next cycle (posted once); send fails (confirmed, not retried); send succeeds; build raises every time (logged each cycle, never confirmed, loop keeps running)
- [ ] Docs: `docs/deployment.md` troubleshooting row for a missing monthly post mentions that a failed build is retried hourly and where it is logged; `README.md` auto-post section stays correct
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-006: Reject unreasonable months up front
**Description:** As a member, I want `/activity month:0001-01` to get the normal month-format reply, not "something went wrong".

**Acceptance Criteria:**
- [ ] `parse_month` / `resolve_month` reject years before `MIN_YEAR = 2015` (a named constant: the year Discord launched) with the same user-readable `InvalidMonthError` message used for other bad months; future months are still rejected as before
- [ ] `Month.previous()` can never raise for a month that passed validation (a month at `MIN_YEAR` January is handled, for example by returning `None`-safe behaviour in `monthly_report` or by `MIN_YEAR` being at least one year above the `Month` lower bound)
- [ ] Timestamp conversion cannot overflow for any accepted month in any valid `ACTIVITY_TIMEZONE`
- [ ] Tests: `0001-01`, `2014-12`, `2015-01` (accepted, report builds), `9999-12`, `2015-13`, a month in `Pacific/Kiritimati` and `Pacific/Pago_Pago`; `/activity` and `/myactivity` reply with the format message for rejected months
- [ ] Docs: `README.md` month parameter text states the earliest accepted month; `help/content.py` is unchanged unless it states a range
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-007: Cheap hourly report check
**Description:** As the owner, I want the hourly "is a report due?" check to stay cheap as months pile up.

**Acceptance Criteria:**
- [ ] The store gains `posted_months() -> set[str]` (or `latest_posted_month()`); `MonthlyReporter.due_month` fetches it once and walks months in memory, calling `_has_data` only for months that are not posted; with every past month posted, a cycle makes at most two store calls (first session and posted months)
- [ ] `monthly_report` and `_has_data` compute `month.start/end(tz).timestamp()` once per call and fetch `sessions_between` for a given window once, not several times
- [ ] New index `CREATE INDEX IF NOT EXISTS ... ON voice_sessions(start_ts)` in the schema (existing databases get it lazily like the other tables)
- [ ] Behaviour is unchanged: oldest unposted month with data first, at most one per call, never older than the first session's month
- [ ] Tests: existing reporter tests keep passing; a counting fake store proves 24 posted months cost at most two store calls; the new index exists after `_connect`
- [ ] Docs: none beyond `README.md` Data and storage if the schema list there names indexes
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-008: Share the auto-post channel helpers
**Description:** As the developer, I want one implementation of "resolve the post channel, warn once per cause, send safely" used by both the CS2 and activity cogs.

**Acceptance Criteria:**
- [ ] New module `src/ninja_bot/posting.py` with `send_safely(awaitable)` and a small `PostChannelResolver(bot, warning_template, channel_id)` (or equivalent) that holds the once-per-cause warning set and implements the `get_channel` → `fetch_channel` → `is_post_channel` → `channel_problem` flow now duplicated in `cogs/cs2.py` and `cogs/activity.py`
- [ ] Both cogs use it; the logged warning texts stay exactly as they are today (`Not posting CS2 matches: ...` / `Not posting the monthly activity overview: ...`); behaviour for missing, wrong-type and permission problems is unchanged and a working channel clears the remembered warnings
- [ ] `fetch_channel` failing with an `HTTPException` other than `NotFound`/`Forbidden` is treated as "unavailable this cycle" (logged at warning level once, no crash) in both cogs
- [ ] Existing cs2 and activity autopost tests pass with only import/attribute updates; new tests for the shared module cover each cause, the clear-on-success rule and the extra `HTTPException` case
- [ ] Docs: `CLAUDE.md` Architecture list names `posting.py`; the layout block in `README.md` lists it
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-009: One helper for splitting intervals at local boundaries
**Description:** As the developer, I want a single, DST-safe way to split a session at local hour and day boundaries.

**Acceptance Criteria:**
- [ ] One function (for example `split_local(start, end, tz, unit: Literal["hour", "day"]) -> Iterator[tuple[float, float, datetime]]`) yields the pieces of `[start, end)` split at local hour or day boundaries, including the "boundary did not advance" guard for DST transitions
- [ ] `_seconds_per_day` (`stats.py`) and `_hour_pieces` (`awards.py`) use it, and the clip-then-iterate loop in `awards._clipped` reuses `clip` from `stats.py` instead of re-implementing it
- [ ] No behaviour change: every existing stats/awards/social test passes unchanged
- [ ] New tests for the helper: within one hour, spanning midnight, spanning several days, the DST spring-forward and fall-back days in `Europe/Amsterdam`, zero-length interval
- [ ] Docs: none (internal)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-010: Remove the unused `tz` parameter from `Month.days`
**Description:** As the developer, I want `Month.days()` to take no argument it ignores.

**Acceptance Criteria:**
- [ ] `Month.days()` has no `tz` parameter and no `del tz`; every call site in `src/` and `tests/` is updated; grep finds no `.days(tz)` or `.days(timezone)` call on a `Month`
- [ ] Behaviour is unchanged
- [ ] Docs: none (internal)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-011: Toasty event mapping out of the Discord handler
**Description:** As the developer, I want the Catch/Cooldown to `PokemonEvent` mapping to be a pure, tested function, per the repo convention that cogs are thin wrappers.

**Acceptance Criteria:**
- [ ] New pure function in `activity/pokemon.py`, `to_event(result: Catch | Cooldown, message_id: int, user_id: int, ts: float) -> PokemonEvent`, containing the `isinstance` branching and the field mapping that today lives in `PokemonTracker.record_reply` (`cogs/activity.py`)
- [ ] `record_reply` only extracts ids and the timestamp from the message, calls `parse_reply` and `to_event`, and stores the result
- [ ] The timestamp choice (message creation time) is passed in and covered by a test with a fake message
- [ ] Tests: `to_event` for a plain catch, a shiny catch and a cooldown (with and without `wait_seconds`); the cog tests keep passing
- [ ] Docs: none (internal)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

## Functional Requirements

- FR-1: The wall of shame lists only users with a voice session in the 90 days before the end of the viewed month, first seen before that month ended, and still members of the server.
- FR-2: For the running month, the current streak ends yesterday when today has no qualifying activity yet.
- FR-3: Biggest climber and Biggest drop are awarded only for completed months.
- FR-4: A monthly report whose construction failed is not marked posted and is retried by the next hourly cycle; a report that was sent (or whose send failed) is marked posted.
- FR-5: Months before 2015-01 are rejected with the standard month-format message.
- FR-6: The hourly report check makes a constant number of store calls once all past months are posted.
- FR-7: CS2 and activity cogs share one channel-resolution and send implementation with unchanged log texts.
- FR-8: Docs listed per story are updated in the same story as the code.

## Non-Goals

- No streaks that continue across month boundaries (documented as per-month instead).
- No opt-out/privacy commands.
- No changes to the voice-tracking, heartbeat or reconcile logic.
- No new commands, settings or storage tables.
- No rarity tracking and no changes to the Toasty parser.

## Technical Considerations

- Same branch (`ralph/activity-tracking`), one commit per story, `./scripts/check.sh` after each.
- Use the existing patterns: `member_lookup`/`MemberCache` in `cogs/cs2.py` for US-002, `cs2/channels.py` for US-008; injected clocks and fixed 2026 dates in tests.
- US-008 touches the CS2 cog: its tests must pass with no behaviour changes.

## Success Metrics

- `./scripts/check.sh` passes after every story.
- Manual (Discord): `/activity` on the dev server shows no departed members on the wall of shame; `/myactivity` keeps the streak in the morning; a forced monthly post still posts once.

## Open Questions

- Should shame candidates later be restricted to a configurable role instead of the 90-day rule?
- Should streaks eventually continue across month boundaries?
