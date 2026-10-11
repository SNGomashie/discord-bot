# PRD: Full player list and Discord names in `/scoreboard`

## Introduction

Two problems with the `/scoreboard` embed:

1. **Only the requester shows up.** The scoreboard should list all players in the match in the two team tables (with the detailed stats, Leetify breakdown and form only for the person who ran the command, which is the intended design). In practice the requester only sees their own information and not the other players. The bot picks the latest match from the player's Leetify match history (`/v3/profile/matches`), and the Leetify spec does not say whether the `stats` array in that response holds all players or only the requested one. The most likely cause is that the history endpoint returns only the requested player, while the single-match endpoint (`/v2/matches/{id}`) returns everyone. Because that is unconfirmed, this PRD adds a diagnostic to the capture script **and** a fix that works whichever way the API behaves.
2. **Friends show up as strangers.** Friends who linked their Steam account and are members of the Discord server should be recognisable: their Discord name in the tables, and a real `@mention` in the detail text.

## Goals

- `/scoreboard` always shows every player in the match in the team tables, whichever Leetify endpoint supplied the match.
- Players the API gave without a team number are never silently dropped.
- Linked friends who are in the server show as their server display name in the tables and as an `@mention` (with their Steam name) in the detail text, without pinging anyone.
- Detailed stats, breakdown and form stay for the requesting player only.
- No new Discord intents (no Server Members intent) and no extra Leetify profile requests.

## User Stories

### US-001: Report player counts in the capture script
**Description:** As the owner, I want to confirm which Leetify endpoints return all players so the fix is based on facts.

**Acceptance Criteria:**
- [ ] `scripts/capture_leetify.py` gets a pure function `summarize_documents(documents) -> list[str]` that reports, for the captured match-history entries and for the single match fetched by id: the match id (anonymised), the number of entries in `stats`, how many distinct `initial_team_number` values there are, and how many players have no team number
- [ ] After capturing, the script prints that summary, plus one line stating plainly whether the history entry has fewer players than the single-match response ("history endpoint returns only the requested player" / "both endpoints return all players" / "could not compare")
- [ ] The summary contains no real Steam IDs, names or the API key (assert in tests, using the existing anonymiser)
- [ ] Unit tests for `summarize_documents` with fixtures: both endpoints full, history with one player, a missing single match, entries with `null` team numbers
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes (ruff, ruff format, mypy strict, pytest)

### US-002: Always use the full match for the scoreboard
**Description:** As a user, I want `/scoreboard` to show all ten players, not just me.

**Acceptance Criteria:**
- [ ] `cs2/client.py` gets a function `full_match(provider: StatsProvider, match: Match) -> Match` (a module-level function, not a Protocol method): it calls `provider.get_match(match.id)` and returns that result if it has at least as many players as `match`; if the call raises a `Cs2Error` or returns fewer players, it logs at INFO (error type only, no body) and returns the original `match`
- [ ] Unexpected exceptions (non-`Cs2Error`) from `get_match` propagate
- [ ] `Cs2._fetch_match` (`cogs/cs2.py`) uses `full_match` for the default path (latest match from `get_recent_matches`); an explicit `match:<id>` already uses `get_match` and is unchanged
- [ ] The extra request only happens for the default path, and `full_match` skips it when `match.teams()` already yields two non-empty teams; a one-player or one-team history entry always triggers it
- [ ] Docstring explains why (the history endpoint may return only the requested player) and that the squad poller must use the same function before announcing a match
- [ ] Unit tests with a fake provider: history entry with one player and a full match by id (full match used), history already complete (no `get_match` call), `get_match` raising `ApiError`/`MatchNotFoundError`/`RateLimitedError` (original returned), `get_match` returning fewer players (original returned), an unexpected exception propagating, and the cog posting a ten-player embed from a one-player history entry
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-003: Never drop players without a team number
**Description:** As a user, I want every player in the match to appear somewhere in the embed.

**Acceptance Criteria:**
- [ ] `Match` gets `unassigned() -> tuple[PlayerMatchStats, ...]` returning the players that `teams()` leaves out (no team number, or a third team number), sorted like a team (best Leetify rating, then kills)
- [ ] `build_match_embed` adds an `Other players` field (non-inline, after the team tables) with `format_team_table(unassigned, max_chars=FIELD_VALUE_LIMIT)` only when there are any; the field respects the 1024 and 6000 character limits and the 25 field cap
- [ ] If neither team has a team number but players exist, the unassigned field still lists them and the title/colour fall back to the neutral case
- [ ] The focus player is still found and their detail fields still render when they are unassigned
- [ ] Unit tests: all players assigned (no extra field), some `None` team numbers, a third team number, no team numbers at all, limits with 10 unassigned players with 32-character names, and the focus player being unassigned
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-004: Reverse link lookup
**Description:** As a developer, I need to find the Discord users for a set of Steam IDs.

**Acceptance Criteria:**
- [ ] `LinkStore` gains `async discord_ids_for(steam64_ids: Collection[str]) -> dict[str, int]` (Steam ID to Discord ID); `SqliteLinkStore` implements it with one query (chunk the `IN (...)` list at 500 values), through `asyncio.to_thread`; empty input returns `{}` without a query
- [ ] If several Discord users linked the same Steam ID (there is no uniqueness constraint) the lowest Discord ID is returned, so the result is deterministic
- [ ] Unknown Steam IDs are simply absent; the Steam IDs are bound as parameters (never formatted into SQL)
- [ ] Fakes of `LinkStore` in the tests get the new method
- [ ] Unit tests on `tmp_path`: found, not found, empty input, duplicate links, more than 500 IDs, and an ID containing quotes or SQL characters
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-005: Resolve match players to Discord members
**Description:** As the owner, I want to know which players in a match are friends in this server, without extra intents.

**Acceptance Criteria:**
- [ ] New module `cs2/identities.py` defines a frozen `Identity(steam64_id, discord_id, display_name)` and `async resolve_identities(players, links, lookup) -> dict[str, Identity]`
- [ ] `lookup` is an injected `Callable[[int], Awaitable[str | None]]` returning the member's display name, or `None` when the user is not a member of the server; the module itself never imports discord
- [ ] Only players with a stored link are looked up; lookups run concurrently (`asyncio.gather`, at most one per distinct Discord ID); a lookup that raises (any exception) is treated as "not a member" and logged without a traceback; one failing lookup never affects the others
- [ ] Players without a link, or whose Discord user is not in the server, are absent from the result
- [ ] The cog provides the `lookup` for the interaction's guild: `guild.get_member(id)` first, then `await guild.fetch_member(id)` (REST, so no Server Members intent is needed); `discord.NotFound` -> `None`, `discord.HTTPException` -> `None` with a log line; in a DM or when `interaction.guild` is `None` the lookup always returns `None` and the link store is not even queried
- [ ] The function is capped to the players of one match (at most 10 lookups), and the display name uses `member.display_name`
- [ ] Unit tests with a fake store and fake lookup: linked member found, linked but not in the server, unlinked, the same Discord user on two Steam IDs, lookup raising, concurrent lookups (assert all started before the first completes), empty players, and the cog lookup with a `MagicMock` guild covering cache hit, fetch fallback, `NotFound`, `HTTPException` and no guild
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-006: Discord names in the team tables
**Description:** As a user, I want my friends to show up by their Discord name in the scoreboard tables.

**Acceptance Criteria:**
- [ ] `format_team_table` takes an optional `names: Mapping[str, str]` (Steam ID to display name); a player with an entry uses that name instead of their Steam name, passed through `clean_name` (backticks and non-printable characters stripped, truncated by display width to `NAME_WIDTH`); everyone else is unchanged
- [ ] With `names=None` or `{}` the output is byte-for-byte what it is today (existing tests keep passing)
- [ ] `build_match_embed` takes an optional `identities: Mapping[str, Identity]` and passes `{steam_id: identity.display_name}` to every table, including the `Other players` field
- [ ] Row width, header alignment, the star and the `max_chars` cap are unchanged
- [ ] Unit tests: a mix of named and unnamed players, a hostile display name (backticks, newline, `@everyone`, very long, CJK/emoji), `names` containing IDs that are not in the table, and alignment (all rows equal display width)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-007: Mentions in the detail text and a friends line
**Description:** As a user, I want to see who my friends are in the match with a real `@mention`, next to their Steam name.

**Acceptance Criteria:**
- [ ] `build_match_embed` adds a non-inline `Friends in this match` field (after the awards, before the focus player's details) when `identities` has at least one entry: one line per friend, `<@discord_id> as SteamName (Your team|Team A|Team B)`, ordered by team then rating, at most 10 lines; the Steam name goes through `escape_name`; omitted when there are no identities
- [ ] The focus player's detail field keeps the Steam name as its title, but its value starts with a line `<@discord_id> · SteamName` when the requester has an identity (always true for a linked member who ran the command in a server), then the existing stats; if the requester is not in `identities` the field is unchanged
- [ ] Detailed stats, the Leetify breakdown and the form strip still render **only** for the focus player; no detail fields are added for other friends (assert in a test with three friends in the match)
- [ ] The cog's `followup.send` for the scoreboard passes `allowed_mentions=discord.AllowedMentions.none()` so mentions render but never ping
- [ ] Mentions only use numeric IDs from the store (never text from Leetify); all other text in these lines is escaped
- [ ] Limits: every field `<= 1024`, total `<= 6000`, `<= 25` fields, asserted for a 10-player match where all players are friends with 32-character names
- [ ] Unit tests: no friends (field absent, output equal to today's), one friend, the requester plus friends, ten friends, a friend on the other team, the field value limit, no detail fields for non-requesters, and the `allowed_mentions` argument
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-008: Wire identities into `/scoreboard`
**Description:** As a user, I want all of this to just work when I run `/scoreboard`.

**Acceptance Criteria:**
- [ ] `_scoreboard_deferred` resolves identities after the match is fetched (the players are only known then): `resolve_identities(match.players, self.links, lookup)` runs in the same deferred step; any exception from it is caught, logged, and the scoreboard is posted without identities (it must never block or fail the scoreboard)
- [ ] The match and profile fetch stay concurrent as before; identity resolution adds no Leetify requests
- [ ] The identity result is passed to `build_match_embed(..., identities=...)` and the followup uses `AllowedMentions.none()`
- [ ] `/profile` is unchanged
- [ ] Unit tests with fakes: end-to-end `handle_scoreboard` with a ten-player match, two linked friends and one of them not in the server (table names, friends field, mention text), the requester only getting details, identity resolution failing (embed still posted without identities), a guild-less interaction, and a history-only one-player match combined with US-002 producing ten players and friend names
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-009: Documentation
**Description:** As the owner, I want the docs to describe the new behaviour and the squad PRD to reuse it.

**Acceptance Criteria:**
- [ ] README `/scoreboard` section: all players appear in the tables, friends who linked and are in the server show by Discord name and as `@mention` (never pinging), detail stats are for the caller only, and the manual checklist gains: a scoreboard where all ten players show up, a match with two linked friends (names + `Friends in this match` line), a friend who left the server (falls back to the Steam name), and a player with no team in the data (`Other players`)
- [ ] README or `docs/deployment.md` notes that no extra Discord intent or permission is needed for this (member lookups use REST)
- [ ] `CLAUDE.md` architecture list gains `cs2/identities.py` and `full_match` in `client.py`
- [ ] `tasks/prd-cs2-squad.md` is updated if it does not already say so: the poller must call `full_match` before building an announcement (US-008 there), and the announcement embed should reuse `resolve_identities` for table names (US-007 there)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

## Functional Requirements

- FR-1: The default `/scoreboard` path fetches the full match by id when the history entry does not already contain two non-empty teams; if that fails it falls back to the history entry.
- FR-2: Every player in the match appears in the embed; players without a usable team number go into an `Other players` field.
- FR-3: Player-to-Discord mapping uses the link store and `guild.get_member` with `fetch_member` as fallback; no new intents, no Leetify requests.
- FR-4: Linked friends who are server members show as their display name in the tables, and as `<@id> as SteamName` in a `Friends in this match` field.
- FR-5: Detailed stats, breakdown and form are shown only for the player who ran the command, headed by their `@mention`.
- FR-6: No mention ever pings (`AllowedMentions.none()`), and mentions are built only from stored numeric IDs.
- FR-7: Any failure in identity resolution is swallowed and logged; the scoreboard is posted without identities.

## Non-Goals

- No detail blocks, breakdowns or profiles for players other than the requester.
- No dropdowns, buttons or other interactive components.
- No `/leaderboard`, trends or auto-posting (`tasks/prd-cs2-squad.md`).
- No Server Members intent and no member cache.
- No change to `/profile`, `/link` or the link store schema.
- No extra Leetify profile requests per scoreboard.
- No change to deployment or prod configuration.

## Design Considerations

- Mentions cannot render inside the monospace code blocks, so the tables show the Discord display name as plain text and the mention appears in normal embed text (the friends field and the first line of the requester's detail block). Field titles do not render mentions either, so the requester's field title stays the Steam name.
- Mentions inside embeds never notify anyone, but `AllowedMentions.none()` is set anyway as defence in depth.
- The fix for the missing players does not depend on the answer to the API question: it uses the history entry when it is already complete, and the single-match endpoint otherwise.

## Technical Considerations

- `full_match` adds at most one Leetify request per default `/scoreboard`; the profile fetch and the match fetch stay concurrent, and the new request happens inside the match fetch.
- `Guild.fetch_member` is a REST call (needs no intent); at most 10 per scoreboard, concurrent, usually served from the `get_member` cache or skipped for unlinked players.
- Several Discord users can link the same Steam ID; the lowest Discord ID wins so output is deterministic.
- If the same Discord user had linked a Steam ID that is not theirs, the effect is only a different display name in the table; nothing sensitive is exposed.
- Keep the order of the other PRDs in mind: `tasks/prd-cs2-vanity-links.md` touches `handle_link` and `setup`, this one touches the scoreboard path; either can merge first. `tasks/prd-cs2-squad.md` should be built after both.

## Success Metrics

- `./scripts/check.sh` passes on the branch after every story.
- Manually, in the dev test server: `/scoreboard` for a real match shows two full teams of players; a linked friend in the server shows by Discord name and `@mention`, strangers show by Steam name; only the requester gets the stats, breakdown and form.
- The capture script's summary line states which Leetify endpoint returns all players.

## Open Questions

- Is the history endpoint really the cause? The capture summary (US-001) answers it. If both endpoints already return all players, the symptom has another cause (for example missing team numbers, handled by US-003) and the real captured data should be looked at before building the rest.
- Should friends who are not in the server (but linked) be listed in the friends field with their Steam name only? Not for now: only server members appear.
