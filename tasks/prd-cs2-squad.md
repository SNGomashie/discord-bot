# PRD: CS2 squad features (`/leaderboard`, trends, auto-posted scoreboards)

## Introduction

Builds on the CS2 scoreboard feature (`tasks/prd-cs2-scoreboard.md`, branch `ralph/cs2-scoreboard`). That feature lets each friend `/link` a Steam account and run `/scoreboard` and `/profile`. This PRD adds the group side:

- `/leaderboard` ranks all linked friends on a chosen metric, with a form trend per player.
- The bot watches for new matches of linked friends and posts a scoreboard embed by itself in a configured channel. One match with several linked friends is posted once, with a squad section highlighting all of them.

Data still comes from the Leetify public API, and everything is rule-based (no LLM calls).

**Prerequisite:** `ralph/cs2-scoreboard` is merged to `main`. This PRD reuses its `cs2/` package (models, `StatsProvider`, `LinkStore`/`SqliteLinkStore`, `formatting.py`, `awards.py`, `embed.py`) and `cogs/cs2.py`, and the config fields `cs2_channel_id`, `cs2_poll_seconds`, `data_dir`. Create the new branch from the updated `main`.

## Goals

- `/leaderboard` shows every linked player ranked on a metric (average Leetify rating by default), with trend arrows, in one embed.
- Trends (recent form against the previous period, streaks) are computed by pure functions over data the API already returns.
- New matches of linked friends appear in the configured channel within roughly one poll interval, exactly once, even across restarts.
- Starting the bot, linking a new friend, or recovering from downtime never floods the channel with old matches.
- The poller can not crash the bot, hammer the API, or spam the channel when something goes wrong.

## User Stories

### US-001: Persist posted matches and seeded players
**Description:** As the owner, I want the bot to remember which matches it already announced so restarts never repost them.

**Acceptance Criteria:**
- [ ] `cs2/store.py` gains a `MatchLog` Protocol: `async is_seeded(steam64_id) -> bool`, `async mark_seeded(steam64_id) -> None`, `async filter_unposted(match_ids: Sequence[str]) -> list[str]` (keeps input order), `async mark_posted(match_ids: Sequence[str]) -> None`
- [ ] `SqliteLinkStore` implements `MatchLog` in the same database file with new tables `posted_matches(match_id TEXT PRIMARY KEY, posted_at REAL NOT NULL)` and `seeded_players(steam64_id TEXT PRIMARY KEY)`; tables are created on first use and an existing `links` table with data is left untouched
- [ ] `mark_posted` prunes the table to the newest 500 rows by `posted_at`
- [ ] `remove`d links keep their `seeded_players` row (re-linking the same account later does not repost old matches)
- [ ] All blocking calls go through `asyncio.to_thread`
- [ ] Unit tests on `tmp_path`: seeded flag round trip, `filter_unposted` order and empty input, `mark_posted` idempotent, pruning at 500 (use a smaller limit via a module constant monkeypatched in the test), data survives a new store instance, and an old database with only `links` upgrades cleanly
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-002: Trends from recent form
**Description:** As a user, I want to see whether a player is trending up or down so the leaderboard tells a story.

**Acceptance Criteria:**
- [ ] `cs2/squad.py` defines a frozen `Trend(direction, delta, streak_kind, streak_length)` and `compute_trend(recent: Sequence[RecentMatch]) -> Trend | None`
- [ ] `recent` is newest first. The trend compares the average Leetify rating of the newest 5 matches with the average of the 5 before them; `direction` is `up` when the delta is at least `+0.01`, `down` at `-0.01` or lower, `flat` otherwise; fewer than 3 usable ratings in either window gives `None`
- [ ] If fewer than 10 matches are available, the older window is whatever remains (minimum 3 usable ratings, otherwise `None`)
- [ ] The streak is the current run of identical outcomes from the newest match (win/loss), ignoring ties and unknown outcomes as streak breakers; `streak_length` is 0 when the newest outcome is unknown or a tie
- [ ] `format_trend(trend) -> str` returns e.g. `↑ +0.04`, `↓ -0.02`, `→`, and appends ` 🔥3` for a win streak of 3+ or ` 🧊3` for a loss streak of 3+; `None` renders as an empty string
- [ ] Matches without a rating are ignored for averages but still count for streaks
- [ ] Unit tests for each direction, the boundary deltas, short histories, missing ratings, streaks of each kind, and formatting
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-003: Squad ranking
**Description:** As a user, I want a pure ranking function so the leaderboard logic is testable.

**Acceptance Criteria:**
- [ ] `cs2/squad.py` defines `Metric` (a `Literal` or `StrEnum`) with values `rating` (average Leetify rating of the profile's recent matches), `aim`, `positioning`, `utility`, `clutch`, `opening`, `winrate`
- [ ] `SquadEntry(discord_id, profile)` (frozen dataclass) and `LeaderboardRow(rank, discord_id, name, value, trend)` where `trend` is a `Trend | None` computed with `compute_trend` (US-002) from the profile's recent matches
- [ ] `rank_squad(entries, metric) -> list[LeaderboardRow]` sorts by value descending; players whose value for the metric is missing are placed last with `value=None` and no rank number problems; ties share the same rank (1, 1, 3) and are ordered by name
- [ ] `metric_label(metric)` and `format_metric_value(metric, value)` return display strings (ratings as signed 2 decimals, skill ratings as 0-100 integers, winrate as `62%`)
- [ ] Unit tests for each metric, ties, missing values, a single entry and an empty list
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-004: Leaderboard embed
**Description:** As a user, I want the squad ranking in one readable embed.

**Acceptance Criteria:**
- [ ] `build_leaderboard_embed(rows, metric) -> discord.Embed` in `cs2/embed.py`
- [ ] Title includes the metric label; rows 1-3 get medal emoji, the rest numbers; each row shows the Discord mention (`<@id>`), the Leetify name, the value from `format_metric_value` and the `format_trend` text; rows with no value show `-`
- [ ] Rows are split across fields of at most 1024 characters each (never splitting a row); at most 25 players are shown with a `+N more` footer when there are more
- [ ] An empty list produces an embed saying nobody is linked yet and pointing to `/link`
- [ ] Total length `<= 6000` and every field `<= 1024` for 25 players with 32-character names (asserted with `len(embed)`)
- [ ] Unit tests for normal, tied, missing-value, empty and oversized inputs
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-005: Short-lived profile cache
**Description:** As the owner, I want repeated leaderboard calls not to burn API quota.

**Acceptance Criteria:**
- [ ] `cs2/cache.py` has a generic `TtlCache[K, V](ttl_seconds, clock=time.monotonic, max_size=128)` with `get(key) -> V | None`, `set(key, value)` and `clear()`; expired entries are dropped on access, and the oldest entry is evicted when `max_size` is exceeded
- [ ] `CachedStatsProvider(inner: StatsProvider, ttl_seconds=300, clock=time.monotonic)` implements `StatsProvider`, caching `get_profile` per Steam ID only; `get_recent_matches` and `get_match` pass through uncached; errors are never cached
- [ ] `setup` in `cogs/cs2.py` wraps the `LeetifyClient` in `CachedStatsProvider`
- [ ] Unit tests with a fake clock: hit, expiry, eviction, error not cached, pass-through methods, and that two different Steam IDs do not collide
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-006: `/leaderboard` command
**Description:** As a user, I want `/leaderboard` to rank everyone who linked their account.

**Acceptance Criteria:**
- [ ] `/leaderboard [metric]` with `app_commands.choices` for each `Metric` (default `rating`) calls `handle_leaderboard`
- [ ] Order: take the per-user cooldown (30 s), `defer(thinking=True)`, load `all_links()`, fetch each profile sequentially through the provider, then rank and post `build_leaderboard_embed`
- [ ] Players whose profile fails with a `Cs2Error` (private, unknown, API error) are skipped and listed in a short footer note (`Skipped: 2 (private or unavailable)`); a `RateLimitedError` stops fetching, posts what was collected so far and adds a note
- [ ] With no links, replies with the empty-state embed and refunds the cooldown; if every fetch failed, replies with an error message and refunds the cooldown
- [ ] At most 25 links are fetched per command; the guard is a module constant
- [ ] Errors outside `Cs2Error` are logged and answered with the generic message; all sends on the deferred path swallow `discord.HTTPException`
- [ ] Unit tests with fakes: normal ranking, each metric choice, skipped players, rate limit midway, no links, all failures, more than 25 links, the cooldown, and the command's choices and description metadata
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-007: Squad highlights in the match embed
**Description:** As a user, I want an auto-posted scoreboard to highlight all the friends who played, not just one.

**Acceptance Criteria:**
- [ ] `format_team_table` accepts an optional `highlight: Collection[str]` of Steam IDs; highlighted players get a `▸` prefix in their row (the name budget shrinks so the row width is unchanged) and the table still respects `max_chars`
- [ ] `build_announcement_embed(match, awards, squad: Sequence[SquadMember]) -> discord.Embed` in `cs2/embed.py`, where `SquadMember(discord_id, steam64_id)`; it reuses the same title, colour (green if the squad majority's team won, red if lost, grey on a mixed or tie result), team tables (with highlights), and awards as `build_match_embed`
- [ ] A `Squad` field lists each friend as `<@discord_id> K/D/A, ADR, rating` (one line each, at most 10), and the embed has no per-player breakdown field
- [ ] Members whose Steam ID is not in the match are ignored; an empty `squad` produces the same output as `build_match_embed` with no focus
- [ ] Limits asserted as before (`<= 1024` per field, `<= 6000` total, `<= 25` fields) for a 10-player match with 5 squad members
- [ ] Unit tests for highlights, colours (win, loss, mixed), missing members, the empty squad and the limits
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-008: Match poller service
**Description:** As the owner, I want a service that finds new matches of linked friends without ever posting twice or flooding the channel.

**Acceptance Criteria:**
- [ ] `cs2/poller.py` defines `NewMatch(match, squad)` and `MatchPoller(provider, store, clock, max_age_seconds=10800, max_per_cycle=3)` where `store` is a `LinkStore` + `MatchLog`
- [ ] `async poll() -> list[NewMatch]` loads all links and handles each linked Steam ID sequentially with `get_recent_matches`
- [ ] A player that is not yet seeded is **seeded**: all their currently returned match IDs are recorded as posted, `mark_seeded` is called, and nothing is announced for them (this makes `/link` and first start silent)
- [ ] For seeded players, matches whose IDs are not yet posted are candidates; candidates older than `max_age_seconds` (from `finished_at`; kept when `finished_at` is unknown) are recorded as posted and dropped
- [ ] A match returned for several linked players is merged into one `NewMatch` listing every linked player who is in it (squad members are the linked Steam IDs present in `match.players`)
- [ ] At most `max_per_cycle` matches are returned, oldest first; the rest stay unposted and are picked up next cycle
- [ ] `poll()` does not mark returned matches as posted; `async confirm(new_match)` does, and the caller calls it after each send attempt (success or failure, so a broken post is never retried forever)
- [ ] A `Cs2Error` for one player (private profile, not found, API error) skips that player and continues; a `RateLimitedError` stops the cycle and returns what was found so far (those players are not marked seeded if their fetch failed); unexpected exceptions propagate
- [ ] Unit tests with a fake provider/store/clock: first poll seeds silently, a second poll announces a new match once, multi-friend dedupe and squad membership, the age filter, `max_per_cycle` ordering and carry-over, per-player errors, rate limit mid-cycle, `confirm` idempotence, and no links at all
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-009: Background auto-post loop
**Description:** As a user, I want the bot to post new scoreboards in the channel by itself.

**Acceptance Criteria:**
- [ ] `cogs/cs2.py` starts a `discord.ext.tasks.loop` in `cog_load` only when `config.cs2_channel_id` is set; the interval is `config.cs2_poll_seconds`; it is cancelled in `cog_unload`; a `before_loop` waits for `bot.wait_until_ready()`
- [ ] The cog method `run_poll_cycle()` holds the logic and is the unit under test: it calls `poller.poll()`, builds `compute_awards` and `build_announcement_embed` for each new match, sends to the channel, then `poller.confirm(new_match)`
- [ ] The channel is resolved from `config.cs2_channel_id` (cache first, then `fetch_channel`); if it is missing, not a text-like channel, or the bot lacks View Channel / Send Messages / Embed Links, a single warning is logged per cause and the cycle does nothing (no crash, no spam)
- [ ] Any exception inside a cycle is caught and logged so the loop keeps running; after a `RateLimitedError` the next cycle is skipped once (simple backoff)
- [ ] Sends use `allowed_mentions=discord.AllowedMentions.none()` so mentions in the squad field never ping
- [ ] When `cs2_channel_id` is not configured, no loop is started and a debug log line says so
- [ ] Unit tests with fakes and a `MagicMock` channel: posting one match, two matches in order, `confirm` called after a `discord.HTTPException` on send, a missing channel, missing permissions, exceptions in `poll`, rate-limit backoff, `allowed_mentions`, and that the loop is only started when a channel is configured
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-010: Documentation
**Description:** As the owner, I want the new features and their setup documented.

**Acceptance Criteria:**
- [ ] README gets a section for `/leaderboard`, the auto-post behaviour (silent seeding of new links, one post per match, 3-hour age limit, at most 3 posts per cycle, poll interval) and a manual test checklist (leaderboard with 2+ linked accounts, a skipped private profile, the auto-post of a new match, no repost after a restart, a match with two linked friends posts once, a new `/link` posts no old matches)
- [ ] `docs/deployment.md` documents `CS2_CHANNEL_ID` and `CS2_POLL_SECONDS` in the settings table, states that the bot now needs **View Channel, Send Messages and Embed Links in the announcement channel** (auto-posts are normal messages, not interaction replies, so channel permissions matter here), notes that `data/cs2.sqlite3` now holds the link list and the posted-match log and should be backed up, and adds troubleshooting rows for "nothing is posted" (channel ID, permissions, privacy mode on Leetify, matches taking time to appear on Leetify) and "old matches reposted" (database file deleted)
- [ ] `CLAUDE.md` architecture list gains `squad.py`, `cache.py`, `poller.py` and the new `store.py` / `embed.py` responsibilities
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

## Functional Requirements

- FR-1: `/leaderboard [metric]` ranks every linked player on the metric, with ties sharing a rank and trend text per player.
- FR-2: Trends compare the newest 5 matches' average Leetify rating with the 5 before, and show win/loss streaks of 3 or more.
- FR-3: The poller checks each linked player's recent matches every `CS2_POLL_SECONDS` (default 300) and announces matches not yet in the posted log.
- FR-4: A match with several linked players is announced once, with all of them highlighted.
- FR-5: A newly linked or first-seen player is seeded silently; matches older than 3 hours are never announced; at most 3 matches are posted per cycle.
- FR-6: Posted match IDs and seeded players survive restarts in `data/cs2.sqlite3`.
- FR-7: Auto-posts happen only when `CS2_CHANNEL_ID` is set and never ping anyone.
- FR-8: The poller handles API errors, rate limits and Discord errors without stopping the bot; a failed post is not retried.
- FR-9: Profile lookups are cached for 5 minutes; match lookups are not.

## Non-Goals

- No per-guild or per-channel configuration commands (the channel is one environment variable).
- No pings, notifications or role mentions; mentions render but never notify.
- No historical backfill or `/scoreboard` for several matches at once.
- No cross-server or multi-guild support (the bot serves one private server).
- No webhooks from Leetify (polling only); no Steam login or OAuth.
- No change to deployment or prod configuration beyond the documentation in US-010.

## Design Considerations

- Reuse `format_team_table`, `compute_awards`, `build_match_embed` and the `Cog` + `handle_*` split from the scoreboard PRD. The announcement embed deliberately omits the per-player breakdown, since there is no single focus player; `/profile` covers that.
- Mentions in embeds (`<@id>`) render as names without needing the Server Members intent and, with `AllowedMentions.none()`, never ping.
- `get_recent_matches` already returns full match details, so the poller does not need a second request per match: one request per linked player per cycle.

## Technical Considerations

- Request budget: the poller makes one API call per linked player every poll interval (for 8 friends at 300 s that is under 100 calls per hour); the leaderboard makes one cached profile call per link.
- Leetify can take a while to show a finished match, so a match appearing late is normal and is still announced as long as it is under the age limit.
- The poll loop runs inside the bot's event loop; all SQLite access uses `asyncio.to_thread`, and the API calls are sequential so one slow request never fans out.
- Auto-posts are normal channel messages, so the bot needs View Channel, Send Messages and Embed Links in that channel (already part of the documented permission integer `52224`).
- The seeding rule is what makes `/link` safe: the first poll after linking records current matches as posted without announcing them.

## Success Metrics

- `./scripts/check.sh` passes on the branch after every story.
- Manually, with the dev bot and two linked accounts: `/leaderboard` renders for each metric, and a new match is posted once in the configured channel within two poll intervals, with no repost after restarting the bot.

## Open Questions

- Should the age limit (3 hours) and the per-cycle cap (3) become environment variables if they turn out to be wrong in practice? Left as module constants for now.
- Should the leaderboard be limited to members currently in the server? It does not check membership today, because that needs either the Members intent or per-member fetches.
