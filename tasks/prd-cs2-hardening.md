# PRD: Harden the CS2 scoreboard feature (code review follow-ups)

## Introduction

A `/code-review` of the `ralph/cs2-scoreboard` branch found ten issues. The most important: `/link` can miss Discord's 3-second acknowledgement window and shows "The application did not respond", a bad match id gets a message about the player, and anyone can burn the shared Leetify quota by spamming `/link`. The rest are smaller: unescaped player names in an embed field, a sequential fetch that doubles worst-case latency, an uncaught decoding error, duplicated helpers, a protocol that needs a special case, table alignment, and `.env.example` comments that promise a feature that does not exist yet. This PRD fixes all ten without adding user-facing features. Every fix keeps the existing behaviour for inputs that already worked.

**Prerequisite:** `ralph/cs2-scoreboard` is merged to `main`. Create `ralph/cs2-hardening` from the updated `main`. This PRD should be merged **before** `tasks/prd-cs2-squad.md` is built, because it changes the `StatsProvider` protocol that the squad work wraps.

## Goals

- `/link` always acknowledges the interaction in time, even when Leetify is slow.
- `/link` cannot be used to drain the shared Leetify API quota.
- Every error message names the thing that was actually not found (player vs match).
- Player names from Leetify can never inject markdown or links into an embed.
- `/scoreboard` latency is the slower of its two requests, not their sum.
- Any upstream fault (bad bytes, bad JSON, timeouts) becomes a friendly `ApiError`, never a stack trace.
- No duplicated helpers or `isinstance` special cases for the provider.
- Tables stay aligned for any name width and any `NAME_WIDTH`.
- `.env.example` no longer promises features that are not built.

## User Stories

### US-001: Defer `/link` before calling Leetify
**Description:** As a user, I want `/link` to always answer, even when Leetify is slow, so I don't see "The application did not respond".

**Acceptance Criteria:**
- [ ] In `handle_link` (`cogs/cs2.py`), the Steam input is parsed first; an `InvalidSteamIdError` is still answered with an ephemeral `send_message` and does **not** defer
- [ ] After parsing, `handle_link` calls `interaction.response.defer(ephemeral=True, thinking=True)` before any request to the stats provider or store, and sends every later reply (success, `Cs2Error`, generic error) with `interaction.followup.send(..., ephemeral=True)`
- [ ] All followup sends go through `_send_safely`, so an expired token or other `discord.HTTPException` is logged and swallowed instead of raised
- [ ] The link is still saved only after the profile lookup succeeds
- [ ] `cog_app_command_error` already uses the followup when `interaction.response.is_done()`; wrap both of its sends in `_send_safely` so a failing send cannot raise out of the error handler
- [ ] Unit tests: defer is called before `get_profile` (assert call order), invalid input never defers, success and each `Cs2Error` type and a generic error use `followup.send` with `ephemeral=True`, an `HTTPException` on the followup is swallowed, and the error handler uses the followup after a defer
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes (ruff, ruff format, mypy strict, pytest)

### US-002: Rate-limit `/link` and skip redundant lookups
**Description:** As the owner, I want `/link` to be unable to drain the shared Leetify quota.

**Acceptance Criteria:**
- [ ] The cog gets a separate `link_cooldown: UserCooldown` (module constant `LINK_COOLDOWN_SECONDS = 15`, injectable like the existing `cooldown`); it is **not** shared with `/scoreboard` and `/profile`
- [ ] Order in `handle_link`: parse input, check the stored link, take the cooldown, defer, call Leetify. Invalid input never takes the cooldown
- [ ] If the caller is already linked to the same SteamID64, `/link` replies ephemerally that they are already linked (naming nothing from Leetify) without calling the provider and without taking the cooldown
- [ ] A user inside the cooldown gets the existing `cooldown_message(retry_after)` as an ephemeral reply and no API call is made
- [ ] The cooldown is **not** refunded after an API call was made (success or failure), so repeated bad lookups are throttled; it is only never taken for invalid or already-linked input
- [ ] A different SteamID64 than the stored one still replaces the link (relinking works)
- [ ] Unit tests with a fake clock: invalid input does not take the cooldown, a repeat call inside the window is rejected with zero provider calls, a call after the window works, an already-linked same id makes zero provider calls, relinking a different id works, `/scoreboard` and `/profile` cooldowns are unaffected by `/link`, and a failed lookup still holds the cooldown
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-003: Distinguish "match not found" from "player not found"
**Description:** As a user, I want a wrong match id to say the match was not found, not that the player is unknown.

**Acceptance Criteria:**
- [ ] `cs2/models.py` adds `MatchNotFoundError(Cs2Error)` with a friendly `default_message` that names the match id problem (e.g. "Leetify has no match with that id. Check the id, or leave `match` empty for the latest match.")
- [ ] In `cs2/client.py`, `_raise_for_status` takes the error to raise for a 404 (default `PlayerNotFoundError`); `get_match` passes `MatchNotFoundError`; `get_profile` and `get_recent_matches` keep `PlayerNotFoundError`
- [ ] Other status mappings are unchanged (403 -> `PrivateProfileError`, 429 -> `RateLimitedError`, other non-2xx -> `ApiError`)
- [ ] `/scoreboard match:<bad id>` shows the new message through the existing `Cs2Error` path
- [ ] Unit tests with a stub `fetch`: 404 on `/v2/matches/...` raises `MatchNotFoundError`, 404 on `/v3/profile` and `/v3/profile/matches` still raise `PlayerNotFoundError`, the other statuses on `get_match` are unchanged, and the cog replies with the match message for an unknown id
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-004: Treat undecodable responses as API errors
**Description:** As the owner, I want upstream faults to be friendly `ApiError`s, not logged stack traces.

**Acceptance Criteria:**
- [ ] In `LeetifyClient._get`, the exceptions caught around the fetch include `UnicodeDecodeError` (raised by `response.text()` on non-UTF-8 or truncated bodies) in addition to `aiohttp.ClientError` and `OSError`; it logs a warning without a traceback and raises `ApiError`
- [ ] The injected `fetch` contract is documented in the `FetchFn` comment: it may raise `aiohttp.ClientError`, `OSError`, `UnicodeDecodeError` or `TimeoutError`, and all of them become `ApiError`
- [ ] The API key never appears in log lines or error messages for this path (assert in the test)
- [ ] Unit tests with a stub `fetch` that raises `UnicodeDecodeError`, plus a regression test for the existing `ClientError`, `OSError`, timeout and non-JSON cases
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-005: One newest-first helper
**Description:** As a developer, I want a single implementation of the newest-first sort so the two copies cannot drift apart.

**Acceptance Criteria:**
- [ ] `cs2/models.py` exposes one public generic helper `newest_first(items)` for any item with a `finished_at: datetime | None`; it sorts newest first, is stable, and keeps items without a time in their original order **after** the dated ones
- [ ] `parse_profile` (recent matches) and `LeetifyClient.get_recent_matches` both use it; `client._newest_first` and `client._EPOCH` are removed, and the unused `datetime` import goes with them
- [ ] Behaviour for existing inputs is unchanged (existing tests keep passing)
- [ ] Unit tests: mixed dated and undated items for both `Match` and `RecentMatch`, equal timestamps keep their order, all-undated input keeps its order, an empty list, and a timezone-aware vs naive-safe check (the helper must not raise `TypeError` on tz-aware times)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-006: Escape player names in embed text
**Description:** As a user, I want a player called `[free nitro](https://evil.example)` or `**x**` to show as plain text, not a link or formatting.

**Acceptance Criteria:**
- [ ] `cs2/formatting.py` adds `escape_name(name: str | None) -> str` that returns the name with `discord.utils.escape_markdown` applied (so `*`, `_`, `~`, `|`, backticks, and the `[` `]` `(` `)` link syntax cannot render), strips control characters and newlines, truncates to 32 characters with `…`, and returns `?` for empty or `None`
- [ ] `build_match_embed` uses `escape_name` for the player name in every award line (`**Title**: name (detail)`)
- [ ] Every other place where a Leetify name is put in embed text **outside** a code block is audited (profile embed title/description, focus-player field name, anywhere in `cogs/cs2.py` messages) and uses the right escaping; names inside code blocks keep using `clean_name`
- [ ] The cog's `linked_message` and `not_linked_message` keep escaping, now via the shared helper where practical
- [ ] Unit tests: masked-link name, `**`, `_`, `~~`, `||spoiler||`, newline and control characters, a 100-character name, an empty or `None` name, and an end-to-end check that the awards field of a built embed contains no unescaped link syntax from a hostile name
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-007: Fetch the match and the profile concurrently
**Description:** As a user, I want `/scoreboard` to be as fast as its slowest request, not the sum of two.

**Acceptance Criteria:**
- [ ] `_scoreboard_deferred` starts the match fetch (`_fetch_match`) and the profile fetch (`_fetch_profile_or_none`) at the same time (`asyncio.gather` or equivalent), for both the latest-match and the explicit `match:` paths
- [ ] Error behaviour is unchanged: a `Cs2Error` from the match fetch is answered with its message and nothing is posted; a `Cs2Error` from the profile fetch still just drops the breakdown; an unexpected exception from either gives the generic message, and no task is left running or unawaited (no "Task exception was never retrieved" warnings)
- [ ] If the match fetch fails, the in-flight profile request is cancelled or its result discarded without logging noise
- [ ] Unit tests with fakes that block on `asyncio.Event`s: both provider calls have started before either finishes; a match error with a slow profile; a profile `Cs2Error` with a good match still posts the embed; an unexpected profile error gives the generic message; the posted embed is the same as before for the success path
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-008: Add `aclose` to the `StatsProvider` protocol
**Description:** As a developer, I want the cog to close any provider the same way, without checking for the concrete class.

**Acceptance Criteria:**
- [ ] `StatsProvider` in `cs2/client.py` gains `async def aclose(self) -> None`; `LeetifyClient.aclose` already satisfies it
- [ ] `Cs2.cog_unload` calls `await self.stats.aclose()` with no `isinstance(..., LeetifyClient)` check, and the `LeetifyClient` import in the cog is removed if no longer needed there (it is still used in `setup`)
- [ ] Test fakes that implement `StatsProvider` get a no-op or recording `aclose`; the shared fake in the CS2 tests records that it was closed
- [ ] A comment on the protocol says wrappers (such as the squad PRD's cache wrapper) must delegate `aclose` to the provider they wrap
- [ ] Unit tests: unloading the cog closes the provider exactly once, closing a client with an injected `fetch` is a no-op, and closing twice is safe
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-009: Fix table alignment (single width constant, display width)
**Description:** As a user, I want scoreboard tables to line up even with long, wide-character or emoji names.

**Acceptance Criteria:**
- [ ] `_ROW_FORMAT` in `cs2/formatting.py` no longer hardcodes `12`; the name column width comes from `NAME_WIDTH` (build the format string from the constant, or pad explicitly), so changing `NAME_WIDTH` keeps header and rows aligned
- [ ] A small helper `display_width(text)` counts East Asian wide and fullwidth characters (`unicodedata.east_asian_width` of `W` or `F`) as 2 columns, combining marks and zero-width characters as 0, and everything else as 1
- [ ] `clean_name` truncates by display width (to `NAME_WIDTH` columns, ending with `…`) and the name is padded with spaces by display width, so a row with CJK or emoji characters has the same visual width as a row with ASCII
- [ ] `clean_name` still strips backticks and non-printable characters and still returns `?` for an empty result; names that fit in `NAME_WIDTH` columns are unchanged
- [ ] The `max_chars` cap in `format_team_table` still holds for the widest case (10 rows of 12 wide characters)
- [ ] Unit tests: ASCII name, 20-character ASCII name, all-CJK name, mixed emoji and ASCII, a combining-mark name, `NAME_WIDTH` monkeypatched to 8 and to 16 (header and every row have equal display width), and the `max_chars` bound
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-010: Honest `.env.example` for unbuilt settings
**Description:** As the owner, I don't want `.env.example` to promise a feature that does not exist yet.

**Acceptance Criteria:**
- [ ] The `.env.example` comments for `CS2_CHANNEL_ID` and `CS2_POLL_SECONDS` say they are reserved for automatic match posts, which are not implemented yet, and that setting them has no effect today; wording matches the README and `docs/deployment.md`
- [ ] `config.py` keeps parsing and validating both values (the squad PRD needs them) with a comment saying the values are reserved; a malformed value still raises `ConfigError` (fail fast), and this is stated in the `.env.example` comment
- [ ] `SqliteLinkStore.all_links` and the `LinkStore.all_links` protocol method stay (the squad PRD uses them); a one-line comment says they are used by the upcoming leaderboard and poller
- [ ] No behaviour change: the existing config tests keep passing
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-011: Documentation
**Description:** As the owner, I want the docs to reflect the new behaviour.

**Acceptance Criteria:**
- [ ] README's `/scoreboard` section: `/link` has a 15-second cooldown that applies after a valid request, re-linking the same account is a no-op, a wrong `match:` id gets a "match not found" reply, and names are escaped; the manual checklist gains: `/link` twice quickly (second is throttled), `/link` with the same account again (already linked), `/scoreboard match:nonsense` (match-not-found message), a player name with markdown in a test match (rendered as plain text)
- [ ] `CLAUDE.md` architecture list mentions `escape_name` / `display_width` in `formatting.py`, `newest_first` in `models.py` and `aclose` on `StatsProvider`
- [ ] `tasks/prd-cs2-squad.md` US-005 says the `CachedStatsProvider` must delegate `aclose` to its inner provider (edit the squad PRD if it does not already say so)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

## Functional Requirements

- FR-1: `/link` validates its input, then defers (ephemeral) before any network or database call; all later replies use the followup.
- FR-2: `/link` has its own 15-second per-user cooldown, taken after valid input and a non-redundant request, and never refunded after an API call.
- FR-3: A 404 from the match endpoint is reported as "match not found"; a 404 from the profile endpoints as "player not found".
- FR-4: Every failure of the Leetify request, including undecodable bodies, is reported as `ApiError` with a friendly message and no traceback in the logs.
- FR-5: There is one implementation of the newest-first sort.
- FR-6: Leetify-controlled text placed in embed text outside a code block is markdown-escaped.
- FR-7: The match and profile are fetched concurrently for `/scoreboard`.
- FR-8: `StatsProvider` includes `aclose`, and the cog closes whatever provider it was given.
- FR-9: Table columns are sized from `NAME_WIDTH` and measured in display columns.
- FR-10: `.env.example` and the docs say the auto-post settings are reserved and have no effect yet.

## Non-Goals

- No new commands, options or visible features beyond the new "already linked" and "match not found" messages.
- No auto-posting, leaderboard or poller (the squad PRD).
- No caching of API responses (the squad PRD's `CachedStatsProvider`).
- No change to the Leetify parsers' field mapping; real-API fixture verification stays a separate manual step (`scripts/capture_leetify.py`).
- No removal of `all_links`, `CS2_CHANNEL_ID` or `CS2_POLL_SECONDS`.
- No change to deployment or prod configuration.

## Technical Considerations

- Reuse the existing `UserCooldown` (`media/cooldown.py`), `cooldown_message`, `_send_safely` and the `Cog` + `handle_*` split; tests use the fake provider, fake store and `MagicMock` interaction already in `tests/test_cs2_cog.py`.
- Interaction acknowledgement: Discord requires a first response within 3 seconds; deferring first gives 15 minutes. The client timeout is 10 seconds, so any Leetify call can exceed the 3-second window.
- For US-007, use `asyncio.gather` carefully: `_fetch_profile_or_none` already swallows `Cs2Error`, so only a match-side failure or an unexpected exception needs cancellation handling.
- For US-009, `unicodedata` is in the standard library, so no new dependency. Emoji and wide characters are a best-effort alignment: Discord's font rendering of some emoji may still differ slightly.
- Stories 001 and 002 touch the same handler, and 008 touches the same protocol the squad PRD wraps, so keep the order.

## Success Metrics

- `./scripts/check.sh` passes on the branch after every story.
- Each of the ten review findings has a regression test that fails on the old code.
- Manually, in the dev test server: `/link` with a slow network still replies; a second `/link` within 15 seconds is throttled; `/scoreboard match:nonsense` gives the match-not-found reply.

## Open Questions

- Is a 15-second `/link` cooldown right? It is only hit by someone retrying quickly, so it is easy to loosen later.
- Should the `StatsProvider` protocol split into a read-only part and a closable part later (for example if a second provider appears)? Not needed while there is one provider.
