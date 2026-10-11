# PRD: Harden the CS2 squad, vanity and identity features (code review follow-ups)

## Introduction

A `/code-review` of everything merged after the first hardening PR (vanity links, scoreboard players and identities, and the squad/leaderboard/auto-post work) found ten issues. Three of them can lose or distort data in production once `CS2_CHANNEL_ID` is set:

- A rate limit or API error during the full-match lookup makes the poller announce an incomplete one-player scoreboard and mark the match posted, so the real scoreboard is never posted.
- The poller's rate-limit handling swallows the error, so the cog's backoff never triggers, and players late in the list are starved every cycle.
- The Steam profile read treats a short network read as the whole body, so a valid custom URL can fail with "Steam couldn't be reached" or "no such custom URL".

The rest are smaller: a member lookup that makes up to 10 REST calls per scoreboard and loses names under rate limits, mentions of people who left the server, inconsistent URL parsing, decision logic living in the cog, and a stale comment. This PRD fixes nine of the ten findings without adding user-facing features. The tenth, serial fetching, is deliberately left as a follow-up (see Non-Goals). Every fix keeps existing behaviour for inputs that already worked.

**Prerequisite:** all earlier CS2 PRDs are merged to `main` (they are). Branch: `ralph/cs2-squad-hardening`.

## Goals

- A match is never announced from incomplete data, and never marked posted unless its announcement was attempted with the full match.
- A Leetify rate limit makes the bot back off, and over several cycles every linked player still gets polled.
- Any valid Steam custom URL resolves regardless of how the network splits the response.
- Scoreboards and announcements make few Discord REST calls, and a rate limit never turns a friend into a stranger.
- Announcements never print a dead mention for someone who has left the server.
- `/link` treats `/profiles/` and `/id/` URLs the same way when they carry a query string or fragment.
- Decision logic lives in pure functions or small services, with the cog as a thin wrapper.

## User Stories

### US-001: Never announce from an incomplete match
**Description:** As a friend in the server, I want the auto-posted scoreboard to contain all players, or to wait until it can.

**Acceptance Criteria:**
- [ ] `full_match(provider, match, *, strict=False)` in `cs2/client.py` gains a keyword-only `strict` flag. With `strict=False` it behaves exactly as today (used by `/scoreboard`: a CS2 error falls back to the history entry)
- [ ] With `strict=True`, a `Cs2Error` from `provider.get_match` is **re-raised** (including `RateLimitedError`, `ApiError` and `MatchNotFoundError`) instead of returning the history entry; the early return for an entry that already has two non-empty teams, and the "fetched result has fewer players, keep the original" rule, are unchanged
- [ ] The docstring explains the two modes and says anything that announces a match must use `strict=True`
- [ ] `MatchPoller.poll()` (`cs2/poller.py`) calls `full_match(..., strict=True)`. If it raises a `RateLimitedError`, the poller stops completing matches for this cycle (handled in US-002). If it raises any other `Cs2Error`, that match is **skipped for this cycle**: it is not returned and **not marked posted**, a line is logged at INFO (match id and error type only), and the other selected matches continue
- [ ] A match skipped this way is retried in the next cycle; it stops being retried once it is older than the existing age limit (`_drop_stale` already marks it posted then)
- [ ] The per-cycle cap (`max_per_cycle`) counts only matches that were completed successfully, so a failing match does not use up a slot
- [ ] Unit tests with a fake provider: complete history entry makes no `get_match` call in either mode; incomplete entry with `strict=False` and an `ApiError` returns the original; the same with `strict=True` raises; `get_match` returning fewer players keeps the original in both modes; the poller skips a match whose lookup fails and returns the others; the failed match is not marked posted and is returned in a later cycle when the lookup works; a permanently failing match is dropped by the age limit; the cap counts only completed matches
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes (ruff, ruff format, mypy strict, pytest)

### US-002: Real rate-limit backoff and fair polling
**Description:** As the owner, I want a Leetify rate limit to make the bot back off, and every linked player to get their turn.

**Acceptance Criteria:**
- [ ] `MatchPoller.poll()` returns a frozen `PollResult(matches: list[NewMatch], rate_limited: bool)` instead of a bare list; `rate_limited` is `True` when a `RateLimitedError` stopped history fetching or full-match completion during the cycle
- [ ] On a rate limit the poller stops fetching further players, still returns the matches already completed (they are safe to announce), and does not mark any unfetched match or player as posted or seeded
- [ ] The poller remembers where a rate-limited cycle stopped (the Steam ID of the player whose request was refused) and starts the **next** cycle from that player, wrapping around the sorted list, so players later in the list are not starved; after a cycle that completes the whole list the starting point resets to the beginning
- [ ] `Cs2.run_poll_cycle` (`cogs/cs2.py`) reads `PollResult`: it announces `matches`, then sets `_skip_next_poll = True` when `rate_limited` is set (one skipped cycle of backoff); the dead `except RateLimitedError` branch is removed
- [ ] The existing behaviour is otherwise unchanged: seeding stays silent, the age limit and `max_per_cycle` still apply, errors for one player skip only that player, unexpected exceptions still propagate from `poll()` and are caught and logged by `run_poll_cycle`
- [ ] Unit tests: a rate limit on the third player of five returns the first two players' completed matches with `rate_limited=True`; the next `poll()` starts at the third player (assert the request order); after a full pass the order resets; a rate limit during full-match completion keeps matches completed before it; no marking happens for unfetched players; the cog skips exactly the next cycle after `rate_limited=True`, announces the partial matches first, and polls again the cycle after that; no backoff when `rate_limited` is `False`
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-003: Read the whole Steam profile response
**Description:** As a user, I want my valid custom URL to resolve however the network chunks the response.

**Acceptance Criteria:**
- [ ] In `cs2/resolver.py`, a new pure-async helper `read_capped(chunks: AsyncIterable[bytes], cap: int) -> tuple[bytes, bool]` collects chunks until the stream ends or the total reaches `cap`, returns at most `cap` bytes, and the bool is `True` only when the stream ended before the cap (the body is complete)
- [ ] `SteamCommunityResolver._aiohttp_fetch` uses it over `response.content.iter_chunked(...)` instead of a single `response.content.read(MAX_BODY_BYTES)`; the cap stays 64 KiB, redirects stay refused, and the timeout is unchanged
- [ ] A complete body must decode fully as UTF-8 (a decode error is still `SteamLookupError`); a body cut at the cap is decoded tolerantly, so a cut in the middle of a multi-byte character does not raise, and an ID found before the cut is still returned while an ID after the cut is not found
- [ ] The `<steamID64>` regex lookup is unchanged and still takes the first match
- [ ] Unit tests for `read_capped` with fake chunk iterators: a body in one chunk, in many small chunks, split exactly inside a multi-byte character (for example a Cyrillic or CJK nickname), exactly `cap` bytes, `cap + 1` bytes, an empty stream; and resolver tests through a stub that returns such bodies: an ID after a nickname that spans a chunk boundary is found, an oversized body reports the ID only if it is within the cap
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-004: Cache Discord member lookups
**Description:** As the owner, I want `/scoreboard` and auto-posts to make few Discord REST calls, and never lose a friend's name because of a rate limit.

**Acceptance Criteria:**
- [ ] `member_lookup` in `cogs/cs2.py` takes the cog's caches and keeps the signature the identities code expects (`Callable[[int], Awaitable[str | None]]`); the order is: cache, `guild.get_member`, `guild.fetch_member`
- [ ] Two `TtlCache` instances from `cs2/cache.py` live on the cog (not module globals): found names for 300 seconds and confirmed "not a member" results for 60 seconds, both keyed by `(guild_id, user_id)` and bounded in size
- [ ] Only definitive results are cached: a display name (from the member cache or REST) and `discord.NotFound`. A `discord.HTTPException`/`Forbidden`/rate limit is **not** cached, is logged without a traceback, and returns `None` as today, so the next scoreboard tries again
- [ ] A friend whose lookup hit a transient error is not cached as a stranger
- [ ] The cache holds only display names the bot already shows; nothing is persisted
- [ ] Unit tests with a `MagicMock` guild and a fake clock: second lookup of the same member makes no `get_member`/`fetch_member` call, a member-cache hit is cached, `NotFound` is cached for 60 s and retried afterwards, an `HTTPException` is not cached and the next call retries, entries expire at their TTLs, two guilds do not collide, and ten concurrent lookups for five distinct users make five REST calls at most
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-005: Announcement Squad field only mentions current members
**Description:** As a friend, I don't want to see a dead `<@123>` mention for someone who left the server.

**Acceptance Criteria:**
- [ ] In `build_announcement_embed` (`cs2/embed.py`), when `identities` is not `None` it is treated as authoritative: a squad member **with** an identity gets the current line (`<@id> as SteamName` plus stats); a squad member **without** one gets a line with no mention (`SteamName` plus the same stats), so their stats still show but nothing dead renders
- [ ] When `identities` is `None` (identity resolution failed or was not attempted), all squad members keep mention lines as today
- [ ] Highlighting in the tables (`▸`) is unchanged and still applies to every linked player in the match
- [ ] `Cs2._match_identities_or_empty` is replaced by a method that returns `None` when resolution **failed** (exception) and a possibly empty dict when it succeeded, so "nobody is a member" and "lookup broke" are different; the scoreboard path (`build_match_embed`) is updated to match without changing its output
- [ ] Limits still hold: every field `<= 1024`, total `<= 6000`, `<= 25` fields, asserted for a squad of 10 with 32-character names
- [ ] Unit tests: all members resolved, one member missing (no mention, stats present), none resolved with `identities={}`, `identities=None`, the mixed case, the limits, and the cog passing `None` on a lookup exception and a dict otherwise
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-006: Same URL tail rules for `/profiles/` and `/id/`
**Description:** As a user, I want a Steam profile link with tracking parameters to work for both URL forms.

**Acceptance Criteria:**
- [ ] In `cs2/steam.py`, one shared constant for the optional trailing part (`/`, then an optional `?...` or `#...` tail) is used by both `_PROFILE_URL_RE` and `_VANITY_URL_RE`
- [ ] `https://steamcommunity.com/profiles/76561198000000000?utm_source=x`, `.../profiles/76561198000000000/#top` and the same forms for `/id/<name>` are accepted and return the same result as without the tail
- [ ] A tail never changes which ID or name is extracted, and other hosts, look-alike hosts (`steamcommunity.com.evil.example`), and the 200-character input cap behave as before
- [ ] Unit tests, parametrized over both URL kinds: `?` tail, `#` tail, `/?` and `/#` tails, an empty `?`, a tail with unicode, and the rejection cases above
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-007: Move the leaderboard fetch policy out of the cog
**Description:** As a developer, I want the leaderboard fetch and skip policy in `cs2/squad.py` so it is tested without a cog.

**Acceptance Criteria:**
- [ ] `SquadFetch`, `leaderboard_notes`, `MAX_LEADERBOARD_LINKS` and the fetch loop (currently `Cs2._fetch_squad`) move to `cs2/squad.py` as `async fetch_squad(provider: StatsProvider, links: Mapping[int, str], limit: int = MAX_LEADERBOARD_LINKS) -> SquadFetch` and `leaderboard_notes(fetch, total_links)`; `cogs/cs2.py` imports and calls them and keeps no policy of its own
- [ ] Behaviour is unchanged: links sorted by Discord ID and capped at `limit`, a `Cs2Error` skips the player and is counted, a `RateLimitedError` stops fetching and records how many were not loaded, unexpected exceptions propagate, entries keep their order
- [ ] `cs2/squad.py` stays free of any `discord` import
- [ ] The existing tests for these behaviours move to `tests/test_cs2_squad.py` against `fetch_squad`/`leaderboard_notes` with a fake provider; the cog tests keep only the wiring (the cooldown, defer, empty and all-failed replies, and the embed being posted)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-008: Move the auto-post channel checks out of the cog
**Description:** As a developer, I want the "can the bot post here?" decision in a small testable module.

**Acceptance Criteria:**
- [ ] New module `cs2/channels.py` has `channel_problem(channel, me) -> str | None`, returning the existing cause strings (`CHANNEL_NOT_TEXT`, `CHANNEL_NO_PERMISSIONS`) or `None` when the bot can post; `None` for a missing channel stays a separate cause handled where the channel is fetched. The module takes the `discord` types it needs (channel types and `Permissions`) and contains no network code
- [ ] The permission rule is unchanged: View Channel, Embed Links and Send Messages (Send Messages in Threads for a `Thread`)
- [ ] `Cs2._post_channel` calls it and keeps only fetching the channel and the log-once bookkeeping; the cause constants move to `cs2/channels.py` and are re-exported or imported by the cog
- [ ] Unit tests in `tests/test_cs2_channels.py` with `MagicMock` channels and real `discord.Permissions` objects: text channel with every permission, each missing permission in turn, a thread with and without Send Messages in Threads, a voice channel, an unsupported type (a `CategoryChannel`), and `me` being a different object than the channel's guild member
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-009: Remove stale comments
**Description:** As a developer, I don't want comments that describe features as upcoming when they exist.

**Acceptance Criteria:**
- [ ] The comment "Used by the upcoming leaderboard and match poller" is removed from `LinkStore.all_links` and `SqliteLinkStore.all_links` in `cs2/store.py`; if a comment is still useful, one line saying that `/leaderboard` and the match poller use it goes on the protocol only
- [ ] Other comments, docstrings and README text that call the leaderboard, poller or auto-posting "upcoming", "reserved" or "not used yet" are found with a search (`grep -rniE "upcoming|reserved|not used yet|not implemented" src README.md docs CLAUDE.md .env.example`) and corrected where they are now wrong; comments that are still true stay
- [ ] No behaviour change
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-010: Documentation
**Description:** As the owner, I want the docs to reflect the new behaviour.

**Acceptance Criteria:**
- [ ] README auto-post section: an announcement waits (is retried next cycle) when the full match can't be fetched, instead of posting a partial scoreboard; after a Leetify rate limit the bot skips one cycle and resumes from the player where it stopped; a friend who left the server appears in the Squad field without a mention; and the manual checklist gains: rate limit simulated or noted as unverifiable, a friend removed from the server, a custom URL with a Cyrillic or CJK Steam nickname, and a profile link with `?utm_source=x`
- [ ] `docs/deployment.md` troubleshooting: a row for "match not posted for a while" (the full match lookup failing or rate limiting; it is retried until the 3-hour age limit)
- [ ] `CLAUDE.md` architecture list gains `cs2/channels.py`, mentions `fetch_squad` in `squad.py`, `read_capped` in `resolver.py` and the `strict` flag of `full_match`
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

## Functional Requirements

- FR-1: The poller announces a match only from a complete `full_match(..., strict=True)` result; on failure the match stays unposted and is retried until the age limit.
- FR-2: A rate limit makes `poll()` return what it completed with `rate_limited=True`; the cog skips the next cycle, and the next poll resumes from the player where the previous one stopped.
- FR-3: The Steam profile body is read until the end of the stream or the 64 KiB cap, never from a single chunk.
- FR-4: Display names and "not a member" results are cached briefly per guild and user; transient Discord errors are never cached.
- FR-5: When identities are known, the announcement's Squad field mentions only users who are currently server members.
- FR-6: `/profiles/` and `/id/` URLs accept the same optional query/fragment tail.
- FR-7: The leaderboard fetch policy and the auto-post channel check are pure or small-module functions with their own tests.

## Non-Goals

- No bounded-concurrency fetching for the poller or `/leaderboard`. Serial requests are fine for a handful of friends; revisit if more than about 8 people link (a follow-up, with a semaphore and the existing rate-limit stop).
- No new commands, options or visible features.
- No change to the Leetify parsers, the link store schema or the posted-match log.
- No persistent cache and no Server Members intent.
- No change to deployment or prod configuration.

## Technical Considerations

- Stories 001 and 002 both change `MatchPoller`: do 001 first. 002 changes the return type of `poll()`, so update every caller and test in the same story.
- The poller's retry behaviour relies on matches staying unposted in the store; `_drop_stale` (age limit 3 h) is the upper bound on retries.
- The cache from `cs2/cache.py` (`TtlCache`) already supports a fake clock; reuse it for US-004 rather than adding a second implementation.
- `aiohttp` `StreamReader.iter_chunked(n)` yields chunks until EOF; `read_capped` keeps the I/O in a tiny helper so the chunk-boundary cases are unit tested without a network.
- Keep the mention rules from earlier PRDs: mentions only from stored numeric IDs, normal embed text only, always `AllowedMentions.none()`.

## Success Metrics

- `./scripts/check.sh` passes on the branch after every story.
- Each finding fixed here has a regression test that fails on the current `main`.
- Manually, in the dev test server: an auto-post of a match always shows two full teams; killing the network for the full-match lookup (for example by pointing at a bad key) delays the post instead of posting a one-player scoreboard.

## Open Questions

- Should repeated full-match failures for the same match be capped (for example 5 attempts) instead of waiting for the 3-hour age limit? Not needed for a handful of friends; the log line makes it visible.
- Should the poller run its per-player history requests concurrently once the group grows? Tracked as the follow-up in Non-Goals.
