# PRD: CS2 skins (inventory worth, top skins, /inventory, /skinboard)

## Introduction

Add Counter-Strike skin features to the CS2 part of the bot: the priciest skins of everyone in a match, the top skins of a player in `/profile`, a new `/inventory` command (total worth + top 5 items) and a `/skinboard` server leaderboard of inventory worth.

Skin data does not exist in the Leetify data, so two new sources are needed:

- **Inventories**: the public Steam inventory endpoint (`steamcommunity.com/inventory/<steam64>/730/2`).
- **Prices**: Steam Market (wallet value, crawled in bulk through `market/search/render`) and Skinport (`api.skinport.com/v1/items`, one bulk call, no key), shown side by side.

Neither Leetify nor Steam exposes which skins a player used in a specific match. "Top skins in the match" therefore means the priciest items in the public inventories of all players in that match (every player has a `steam64_id` in the Leetify match stats), not their in-game loadout.

Approved plan: `/home/senag/.claude/plans/in-an-branch-i-swirling-pinwheel.md`. All logic stays in pure modules under `src/ninja_bot/cs2/`; the cog stays a thin wrapper (see CLAUDE.md conventions).

## Goals

- Show the top 3 priciest skins in the match summary (`/scoreboard` and auto-posted matches), across all players with a public inventory.
- Show the top 3 priciest skins and the inventory worth in `/profile`.
- `/inventory` shows total worth and the top 5 items for a linked member, a Steam profile link/ID/custom URL name, or the caller.
- `/skinboard` ranks linked members by inventory worth.
- Always show Steam Market and Skinport prices next to each other.
- Never break existing replies and posts: a skin failure only drops the skin section.
- Stay inside Steam's rate limits through caching, pacing and a stored price book.

## User Stories

### US-001: Inventory models and parsing
**Description:** As a developer, I need to turn a Steam inventory response into typed items so later features can value them.

**Acceptance Criteria:**
- [ ] New `src/ninja_bot/cs2/inventory.py` with frozen dataclass `InventoryItem` (`market_hash_name`, `name`, `item_type`, `marketable`, `colour`, `kind`) and `parse_inventory(payload) -> list[InventoryItem]`, joining `assets` to `descriptions` by `classid` + `instanceid`
- [ ] `kind` is an enum `ItemKind` (`SKIN` for weapons, knives and gloves; `OTHER` for stickers, cases, charms, graffiti, music kits etc.), derived from the item `tags` (category `Type`) with a documented fallback on `type`
- [ ] An empty or `null` inventory parses to an empty list; malformed payloads raise `ApiError` (reuse `models.py` error types)
- [ ] Hand-written fixture `tests/fixtures/steam/inventory.json` (weapon skin, knife, gloves, sticker, case, a non-marketable item, a stacked case) and tests in `tests/test_cs2_inventory.py` covering each kind, stacks and malformed input
- [ ] Add `PrivateInventoryError(Cs2Error)` to `models.py` with a user-facing message
- [ ] Docs: CLAUDE.md architecture entry for `inventory.py`
- [ ] Typecheck, lint and tests pass (`./scripts/check.sh`)

### US-002: SteamInventoryClient
**Description:** As a developer, I need a client that fetches a player's CS2 inventory so commands and auto-posts can value it.

**Acceptance Criteria:**
- [ ] `InventoryProvider` Protocol (`get_inventory(steam64_id) -> list[InventoryItem]`, `aclose`) in `inventory.py`
- [ ] `SteamInventoryClient` with injectable fetch (same `FetchResponse`/`FetchFn` shape as `client.py`), lazy aiohttp session, timeout, `aclose`
- [ ] Requests `https://steamcommunity.com/inventory/<id>/730/2?l=english&count=2000`; bodies read with `read_capped` from `resolver.py` with an explicit cap (a constant, larger than the resolver's), never a single `content.read(n)`
- [ ] Status mapping: 403 -> `PrivateInventoryError`; 429 -> `RateLimitedError`; 404 and an empty inventory -> empty list; timeouts, network errors, other statuses, undecodable bodies -> `ApiError`
- [ ] The steam64 id is validated (digits, `parse_steam_id`-style) before it goes into the URL
- [ ] Tests in `tests/test_cs2_inventory_client.py` using the `StubFetch` pattern: success, each status, timeout, oversize body, invalid id
- [ ] Docs: CLAUDE.md architecture
- [ ] Typecheck, lint and tests pass

### US-003: Cached and paced inventory provider
**Description:** As a developer, I need inventory lookups cached and rate-paced so Steam's per-IP limit is respected.

**Acceptance Criteria:**
- [ ] `CachedInventoryProvider(InventoryProvider)` wrapping the client with a `TtlCache` (`INVENTORY_TTL_SECONDS` = 1800, success only; private and other errors are never cached except as below)
- [ ] Private inventories are cached for a shorter `PRIVATE_TTL_SECONDS` (600) as a negative entry so a match with private players does not re-fetch them
- [ ] An `asyncio.Lock` serialises cache misses and waits at least `MIN_FETCH_INTERVAL_SECONDS` (4) between network fetches (injectable clock and sleep); cache hits never wait
- [ ] After a `RateLimitedError`, further network fetches fail fast with `RateLimitedError` for `RATE_LIMIT_BACKOFF_SECONDS` (300) without calling Steam
- [ ] `aclose` delegates to the wrapped provider
- [ ] Tests in `tests/test_cs2_inventory_cache.py`: hit, miss, spacing, negative cache, backoff, aclose delegation
- [ ] Docs: CLAUDE.md architecture; README Caching table gets a row (TTL values taken from the constants)
- [ ] Typecheck, lint and tests pass

### US-004: Price types, store and PriceBook
**Description:** As a developer, I need prices persisted and quickly looked up so valuing an inventory needs no network call.

**Acceptance Criteria:**
- [ ] New `src/ninja_bot/cs2/prices.py` with `Prices(steam: int | None, skinport: int | None)` (minor units of the configured currency) and `PriceBook` (in-memory `dict[str, Prices]`, `get(name)`, `is_empty`, `has_steam`, `has_skinport`, `update_steam(mapping)`, `update_skinport(mapping)`)
- [ ] `PriceStore` Protocol (`load_prices`, `save_steam_prices`, `save_skinport_prices`, `last_update(source)`) implemented by `SqliteLinkStore` in `cs2/store.py` using a lazy `prices` table (`market_hash_name` primary key, `steam_minor`, `skinport_minor`, `steam_updated_at`, `skinport_updated_at`), blocking calls via `asyncio.to_thread` like the existing methods
- [ ] Saving one source never clears the other source's column
- [ ] Tests in `tests/test_cs2_prices.py` and `tests/test_cs2_store.py` (extend): round trip, partial update, empty table
- [ ] Docs: README Data and storage (new table), CLAUDE.md architecture; `docs/deployment.md` backup notes if they list tables
- [ ] Typecheck, lint and tests pass

### US-005: SKIN_CURRENCY config
**Description:** As the bot owner, I want to choose the price currency so prices match what we use.

**Acceptance Criteria:**
- [ ] `Config.skin_currency` from env `SKIN_CURRENCY` (default `EUR`, case-insensitive); an invalid value raises the same config error type as other bad values (see `_parse_timezone`)
- [ ] A `Currency` table in `prices.py` (code, symbol, Steam currency id, Skinport code) containing only currencies both Steam and Skinport support (at least USD, EUR, GBP; each id/code verified against the Steam and Skinport docs and noted in a comment)
- [ ] `format_price(minor, currency)` formats minor units with the symbol (`€1,234.50`), `None` -> `n/a`
- [ ] Tests in `tests/test_config.py` and `tests/test_cs2_prices.py`
- [ ] Docs: README Configuration table, `.env.example`, `docs/deployment.md` settings reference
- [ ] Typecheck, lint and tests pass

### US-006: Skinport price client
**Description:** As a developer, I need to fetch Skinport prices in one bulk call.

**Acceptance Criteria:**
- [ ] `uv add Brotli` (so aiohttp can decode `Content-Encoding: br`); `uv.lock` committed
- [ ] `SkinportClient` with injectable fetch, sending `Accept-Encoding: br` and a descriptive `User-Agent`; calls `https://api.skinport.com/v1/items?app_id=730&currency=<code>&tradable=0`
- [ ] `parse_skinport(payload, currency) -> dict[str, int]` maps `market_hash_name` to `min_price` in minor units; items with a null `min_price` are omitted; the minor-unit conversion uses `Decimal` (no float drift)
- [ ] The body (about 10 MB decoded) is read with `read_capped` and an explicit cap constant (`SKINPORT_MAX_BODY_BYTES`, 32 MiB); 429 -> `RateLimitedError`; other failures -> `ApiError`
- [ ] Hand-written fixture `tests/fixtures/skinport/items.json` (priced item, null price item, stack of items) and tests in `tests/test_cs2_skinport.py`
- [ ] Docs: CLAUDE.md architecture
- [ ] Typecheck, lint and tests pass

### US-007: Steam Market price client
**Description:** As a developer, I need to crawl the Steam Market price list gently so we have wallet-value prices.

**Acceptance Criteria:**
- [ ] `SteamMarketClient` with injectable fetch, `fetch_page(start) -> MarketPage(total_count, prices)` for `https://steamcommunity.com/market/search/render/?appid=730&norender=1&sort_column=price&sort_order=desc&start=<n>&count=100&currency=<steam id>`
- [ ] `parse_market_page` maps `hash_name` to `sell_price` (minor units); entries with a missing or zero price are omitted; `success: false` or a malformed body raises `ApiError`
- [ ] A `crawl(on_page)` method walks the pages with `MIN_PAGE_INTERVAL_SECONDS` (5) between requests (injectable sleep), stops at `total_count` or `MAX_PAGES` (500), and retries a 429 page after a backoff, giving up after `MAX_RETRIES` and returning the pages gathered so far with a flag that the crawl was incomplete
- [ ] Hand-written fixture `tests/fixtures/steam/market_search.json` and tests in `tests/test_cs2_steam_market.py`: parsing, pagination, 429 backoff, incomplete crawl
- [ ] Docs: CLAUDE.md architecture
- [ ] Typecheck, lint and tests pass

### US-008: PriceRefresher
**Description:** As a user, I want prices kept fresh automatically without anyone running a command.

**Acceptance Criteria:**
- [ ] `PriceRefresher(book, store, steam_client, skinport_client, clock)` in `prices.py` with `refresh_skinport()` and `refresh_steam()`; each updates the book and the store, and a failure of one source never affects the other or clears existing prices
- [ ] `due()` decides what is due: Skinport when missing or older than `SKINPORT_REFRESH_SECONDS` (3600), Steam when missing or older than `STEAM_REFRESH_SECONDS` (86400); an incomplete Steam crawl still saves what it got and retries after `STEAM_RETRY_SECONDS` (3600)
- [ ] `load()` fills the `PriceBook` from the store at start
- [ ] `run_cycle()` never raises (each step guarded and logged without secrets)
- [ ] The cog starts a `tasks.loop` (every 10 minutes) running `run_cycle`; started in `cog_load`/`setup` and cancelled in `cog_unload`; clients are closed in `cog_unload`
- [ ] Tests in `tests/test_cs2_price_refresher.py` with fake clients and clock: due logic, independent failures, incomplete crawl
- [ ] Docs: README feature section stub "Skins" explaining the price sources and refresh intervals (values from constants); CLAUDE.md architecture
- [ ] Typecheck, lint and tests pass

### US-009: Valuation
**Description:** As a developer, I need a pure function that values an inventory so every command and embed shares one definition.

**Acceptance Criteria:**
- [ ] New `src/ninja_bot/cs2/valuation.py` with `ValuedItem` (item name, count, `Prices`, owner steam64 id optional) and `Valuation(steam_total, skinport_total, item_count, priced_count, top)`
- [ ] `value_inventory(items, book, top_n)` ignores non-marketable items, groups stacks by `market_hash_name`, counts every marketable item towards the totals (price x count, per source, a missing price contributes 0 to that source's total), and builds `top` only from `ItemKind.SKIN` items
- [ ] `top` is ranked by Steam price falling back to Skinport price, ties broken by name; items with no price at all are excluded from `top` and counted in `item_count - priced_count`
- [ ] `match_top_skins(inventories: Mapping[str, list[InventoryItem]], book, top_n=3)` returns `ValuedItem`s with the owner steam64 id, ranked the same way, across all given inventories
- [ ] Tests in `tests/test_cs2_valuation.py`: stacks, fallbacks, one-source-only, ties, empty inventories, non-marketable
- [ ] Docs: CLAUDE.md architecture
- [ ] Typecheck, lint and tests pass

### US-010: Skin embed pieces
**Description:** As a user, I want skin prices shown clearly with both sources side by side.

**Acceptance Criteria:**
- [ ] `format_prices(prices, currency)` -> `Steam €12.30 · Skinport €10.05` (`n/a` for a missing source) in `cs2/formatting.py` or `embed.py`
- [ ] `skins_field_lines(valued, currency, owner_names=None)` builds the lines for a `Top skins` field: item name (via `escape_name`, `max_length` chosen to fit), prices, and the owner name when `owner_names` is given; capped with `fit_lines`-style `+N more` and `FIELD_VALUE_LIMIT`
- [ ] `build_inventory_embed(name, valuation, currency, steam_url?)` for `/inventory`: title with the escaped name, `Total worth` field (`Steam €X · Skinport €Y`), `Items` field (marketable count, unpriced count), `Top 5 items` field; empty inventory -> description message
- [ ] `build_profile_embed`, `build_match_embed` and `build_announcement_embed` accept an optional `skins` argument (default `None`) that adds the `Top skins` field (and, for the profile, a worth line); with `None` their output is unchanged (existing tests untouched and passing)
- [ ] Embed tests in `tests/test_cs2_embed.py` (extend): each builder with and without skins, long names truncated, field limits respected, no mentions in match posts
- [ ] Docs: CLAUDE.md `embed.py` entry
- [ ] Typecheck, lint and tests pass

### US-011: /inventory command
**Description:** As a user, I want to run `/inventory` to see the worth and the top 5 items of my inventory, another member's, or any Steam profile.

**Acceptance Criteria:**
- [ ] `/inventory [player] [steam]`: `player` is a `discord.Member`, `steam` is a Steam profile URL, SteamID64 or custom URL name; giving both is rejected ephemerally; giving neither uses the caller
- [ ] `steam` is parsed with `parse_steam_target` and, for a custom URL name, `resolver.resolve_vanity` (same errors/messages as `/link`); a `player` or the caller without a link gets the existing not-linked message
- [ ] Uses the shared price book; when it is empty the reply (ephemeral) says prices are still loading and the cooldown is refunded
- [ ] Private inventory -> `PrivateInventoryError` message; rate limited -> the existing rate-limit wording; other failures -> `GENERIC_ERROR_MESSAGE`; none leak internals
- [ ] Own `UserCooldown` (`INVENTORY_COOLDOWN_SECONDS` = 30), refunded unless an embed was posted; defers publicly; embeds are sent with `AllowedMentions.none()`
- [ ] Cog wiring in `setup`: `CachedInventoryProvider(SteamInventoryClient())`, `PriceBook`, `PriceRefresher`; `cog_unload` closes the inventory provider
- [ ] `help/content.py` entry for `inventory`; `tests/test_help_drift.py` passes (Discord-side `description=` on the command and both parameters)
- [ ] Cog tests in `tests/test_cs2_cog.py` (extend, fakes for inventory and prices): self, member, Steam URL, vanity, both given, not linked, private, prices loading, cooldown refund
- [ ] Docs: README commands table row, a `### /inventory` section (parameters, formats, cooldown and refund rules, reply visibility, errors, caveats), Error replies table, `docs/manual-checks.md` section, CLAUDE.md cog entry
- [ ] Typecheck, lint and tests pass

### US-012: Skins in /profile
**Description:** As a user, I want `/profile` to show my inventory worth and my top 3 skins.

**Acceptance Criteria:**
- [ ] `_profile_deferred` fetches the inventory in the same flow, valued with the price book, and passes `skins` to `build_profile_embed`
- [ ] If the inventory is private, rate limited, fails, or prices are not loaded, the profile is posted unchanged (the failure is logged at debug/warning without secrets) and the cooldown behaviour is unchanged
- [ ] The inventory fetch has a timeout (`SKIN_FETCH_TIMEOUT_SECONDS`, 15) so a slow Steam never delays the profile by more than that
- [ ] Cog tests: skins shown, private inventory, error, timeout, prices loading
- [ ] Docs: README `/profile` section and commands table description, `help/content.py` `profile` entry (details mention skins), `docs/manual-checks.md`
- [ ] Typecheck, lint and tests pass

### US-013: Match top-3 skins helper
**Description:** As a developer, I need one helper that gathers inventories of everyone in a match within a time budget.

**Acceptance Criteria:**
- [ ] `fetch_match_inventories(provider, steam_ids, budget_seconds, clock)` in a cs2 module (no discord import) returns `dict[steam_id, list[InventoryItem]]` with the players that were fetched; private, unavailable and rate-limited players are skipped; it stops starting new fetches once the budget (`MATCH_SKINS_BUDGET_SECONDS`, 30) is used up and returns what it has
- [ ] Rate limited -> stop immediately (no more calls); never raises
- [ ] Ids are deduplicated, order preserved
- [ ] Tests in `tests/test_cs2_match_skins.py`: all public, mixed private, budget exhausted, rate limited, duplicates
- [ ] Docs: CLAUDE.md architecture
- [ ] Typecheck, lint and tests pass

### US-014: Top skins in /scoreboard and auto-posts
**Description:** As a user, I want match summaries to show the 3 priciest skins in the match.

**Acceptance Criteria:**
- [ ] `_scoreboard_deferred` and `_announce` call `fetch_match_inventories` for all players of the match (`match.players` steam ids), `match_top_skins`, and pass `skins` (with owner names from the match's player names via `clean`/`escape_name`) to `build_match_embed` / `build_announcement_embed`
- [ ] Skin failures or an empty result leave the embed unchanged; the `confirm` call in the poller and the posting path behave exactly as before
- [ ] No mentions are added by the skins field; `AllowedMentions.none()` stays
- [ ] When the price book is empty the skins step is skipped without fetching inventories
- [ ] Cog and autopost tests (`tests/test_cs2_cog.py`, `tests/test_cs2_autopost.py`): field present with owners, field absent on failure, price book empty -> no inventory calls, budget respected
- [ ] Docs: README `/scoreboard` numbered list and "Auto-posted matches" section, Known limitations (public inventories only, ~40 s cold, budget), `help/content.py` `scoreboard` entry, `docs/manual-checks.md`
- [ ] Typecheck, lint and tests pass

### US-015: /skinboard
**Description:** As a user, I want a leaderboard of inventory worth across the linked members of the server.

**Acceptance Criteria:**
- [ ] `/skinboard` lists linked guild members ranked by inventory worth (Steam total first, Skinport total next to it), shared ranks for ties (reuse `rank_squad`/`LeaderboardRow` from `squad.py` if the shape fits, otherwise add `rank_worth` beside it), capped at `SKINBOARD_LIMIT` (25) members like `/leaderboard`
- [ ] Private or failed inventories are skipped and counted in a footer note (`n skipped`), never failing the command
- [ ] Defers publicly, `AllowedMentions.none()`, own cooldown (`SKINBOARD_COOLDOWN_SECONDS` = 300) refunded unless posted; prices loading -> ephemeral message and refund
- [ ] A time budget like US-013 bounds the fetching; the result says if it was cut short
- [ ] `build_skinboard_embed` in `embed.py` with an empty-state description, names via `escape_name`
- [ ] `help/content.py` entry; `tests/test_help_drift.py` passes
- [ ] Tests: ranking, ties, skipped, cap, budget, cooldown refund, empty state
- [ ] Docs: README commands table row and `### /skinboard` section, `docs/manual-checks.md`, CLAUDE.md
- [ ] Typecheck, lint and tests pass

### US-016: Capture script and final docs
**Description:** As the maintainer, I want to verify the hand-written fixtures against the real services and have all docs match the final code.

**Acceptance Criteria:**
- [ ] `scripts/capture_steam.py` (manual, dev-only, like `scripts/capture_leetify.py`) fetches one public inventory (SteamID given as an argument), one Steam Market page and the Skinport list into `data/steam-capture/`, anonymising the SteamID; it never reads `.env` or prints tokens
- [ ] README: feature bullet at the top, a complete `### Skins` section (price sources, refresh intervals, currency, `n/a`, top-list rules), Configuration, Data and storage, Caching, Error replies, Known limitations (private inventories, storage units, name-only pricing, first crawl ~30 min, Steam IP limit, `min_price` listings), Layout block, Development (the new script)
- [ ] CLAUDE.md Architecture lists every new module and the cog changes
- [ ] `docs/deployment.md` (env var, outbound access to `steamcommunity.com` and `api.skinport.com`, backup note), `docs/manual-checks.md` (checklist for all skin features including a mix of public/private inventories), `.env.example`
- [ ] `CHANGELOG.md` entry is deferred to the PR (per workflow) but the "Known follow-ups" are listed in `progress.txt`
- [ ] README relative links and anchors resolve; every constant quoted in the docs is read from the code
- [ ] `./scripts/check.sh` passes

## Functional Requirements

- FR-1: Inventories come from `steamcommunity.com/inventory/<steam64>/730/2`; only public inventories can be read.
- FR-2: Prices come from Steam Market (bulk crawl of `market/search/render`, wallet value) and Skinport (`/v1/items`, `min_price`); both are shown for every price, `n/a` where a source has no price.
- FR-3: The currency is configured by `SKIN_CURRENCY` (default `EUR`) and applied to both sources.
- FR-4: Prices live in an in-memory `PriceBook` backed by SQLite; Skinport is refreshed hourly, Steam daily; failures keep the last good prices.
- FR-5: Total worth counts every marketable item; top lists contain weapon skins, knives and gloves only, ranked by Steam price with a Skinport fallback.
- FR-6: Match summaries (`/scoreboard`, auto-posts) show the top 3 priciest skins across all match players with public inventories, each credited to the player's name, with no mentions.
- FR-7: `/profile` shows the inventory worth and the top 3 skins of the profile's player.
- FR-8: `/inventory [player] [steam]` shows total worth, item counts and the top 5 items.
- FR-9: `/skinboard` ranks linked members by inventory worth, max 25.
- FR-10: Inventory fetches are cached (30 min), paced (at least 4 s between network fetches) and back off after a 429 (5 min).
- FR-11: Any skin failure (private, rate limit, network, prices not loaded) leaves existing replies and posts unchanged apart from missing the skin section.
- FR-12: Commands have their own cooldowns, refunded unless a result was posted.

## Non-Goals

- No in-game loadout detection or per-match skin usage (not available from any API).
- No float, pattern, Doppler phase, sticker-value or wear-based pricing; prices are by `market_hash_name`.
- No inventory value history, weekly change or notifications.
- No Steam login, cookies or API key; no item trading or listing.
- No items inside storage units; no CS:GO (appid 730 context 2 only).
- No LLM calls.
- No change to deployment or prod configuration beyond documenting the new env var.

## Design Considerations

- Embeds follow the existing style: `escape_name` for names in embed text, `clean_name` in code blocks, `_add_field` skips empty values, values capped at `FIELD_VALUE_LIMIT`.
- Price display: `Steam €1,234.50 · Skinport €1,100.00`; the top-skins lines in match posts also show the owner, e.g. `1. ★ Karambit | Fade (Factory New) · Steam €2,100 · Skinport €1,900 · by PlayerName`.
- `/inventory` mirrors `/profile` for target selection and `/leaderboard` for list layout.

## Technical Considerations

- Steam inventory endpoint is limited per IP (community reports: about one request per 4 s, 429 lockouts of hours). The paced cache, negative cache and backoff in US-003 exist for this. The values are conservative defaults and should be verified with `scripts/capture_steam.py`.
- Skinport requires brotli; the `Brotli` package is a new dependency (pin via `uv add`). Its bulk response is about 10 MB decoded, so read with an explicit cap and parse once per hour.
- Steam Market crawl is ~355 pages of 100; run at one page per 5 s (about 30 minutes) once a day, resumable by saving partial results.
- Blocking SQLite calls go through `asyncio.to_thread`, like `cs2/store.py`.
- Hand-written fixtures, as with Leetify, must be checked against real responses before release (manual check).
- Cogs stay thin: fetch/valuation/formatting logic lives in `cs2/` modules without a discord import.

## Success Metrics

- `./scripts/check.sh` passes at every story.
- A match post with no public inventories and a post with prices not loaded are identical to today's output.
- `/inventory` on a public inventory returns in one defer; a second call within 30 minutes makes no Steam request.
- In the dev server a cold match with 10 players posts within about 35 seconds (30 s budget).

## Open Questions

- Exact list of currencies supported by both Steam and Skinport (resolved in US-005 from their docs).
- Whether `count=2000` on the inventory endpoint returns everything for large inventories or needs pagination (`last_assetid`/`more_items`); verify with `scripts/capture_steam.py` and add pagination only if needed.
- Whether the Steam Market search accepts `count=100` consistently without a login (the check so far worked for 5 results).
