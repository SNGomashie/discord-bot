# PRD: Accept Steam vanity URLs in `/link`

## Introduction

`/link` currently only accepts a 17-digit SteamID64 or a `steamcommunity.com/profiles/<id>` URL. Most people only know their custom profile URL (`steamcommunity.com/id/<name>`), so linking is annoying: the bot rejects it and tells them to dig the number out of Steam. Steam's official resolver (`ISteamUser/ResolveVanityURL`) needs an API key, but the public profile page returns the SteamID64 without one at `https://steamcommunity.com/id/<name>/?xml=1`. This PRD makes `/link` accept vanity URLs and bare vanity names by resolving them through that keyless endpoint. The endpoint is unofficial, so every failure falls back to a friendly message that points at the 17-digit ID.

## Goals

- `/link https://steamcommunity.com/id/gaben`, `/link steamcommunity.com/id/gaben/` and `/link gaben` all link the right account.
- The existing inputs (SteamID64, `/profiles/<id>` URL) behave exactly as before, with no extra network call.
- A vanity name that does not exist, a Steam outage or a rate limit each give a clear, friendly reply that never exposes a traceback.
- Resolving a name can not be abused to make the bot request arbitrary URLs or read unbounded data.
- No new API key, dependency or setting is needed.

## User Stories

### US-001: Parse vanity names from `/link` input
**Description:** As a user, I want `/link` to understand my custom Steam URL or name so I don't have to look up a number.

**Acceptance Criteria:**
- [ ] `cs2/steam.py` replaces `parse_steam_id` with `parse_steam_target(text) -> SteamId | VanityName`, where `SteamId` and `VanityName` are small frozen dataclasses with one `value: str` field (`parse_steam_id` may stay as a thin wrapper that raises for vanity input, only if other code still needs it; otherwise remove it and update its callers and tests)
- [ ] A bare SteamID64 (`7656119` + 10 digits) and a `steamcommunity.com/profiles/<id>` URL still return `SteamId` exactly as before (same URL variants: optional scheme, `www.`, trailing slash, any case)
- [ ] A `steamcommunity.com/id/<name>` URL (same URL variants, and a `?` query string or `#` fragment is ignored) returns `VanityName(name)`; the name is the path segment only
- [ ] A bare string that is a valid vanity name returns `VanityName`; valid means 2 to 32 characters from `A-Z a-z 0-9 _ -`, ASCII only (`re.ASCII`). The name is kept as typed (Steam treats it case-insensitively)
- [ ] Anything else raises `InvalidSteamIdError` with a message that lists the accepted forms: SteamID64, `/profiles/<id>` link, `/id/<name>` link, or the custom URL name
- [ ] A 17-digit number that does not start with `7656119` is rejected as an invalid ID and is **not** treated as a vanity name; other all-digit strings of 2 to 32 characters are valid vanity names (Steam allows numeric custom URLs) except those that are 17 digits long
- [ ] The existing 200-character input cap runs before any regex; the `VANITY_URL_MESSAGE` constant and the rejection of `/id/` URLs are removed
- [ ] Unit tests, parametrized: each accepted SteamID64/profile URL variant, each vanity URL variant, bare names at the 2 and 32 character bounds and just outside them, names with spaces, `/`, `.`, `@`, unicode letters and digits, empty and whitespace-only input, a 17-digit non-Steam number, input over 200 characters, a URL on another host (`evil.example/id/gaben`), and `steamcommunity.com.evil.example/id/gaben`
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes (ruff, ruff format, mypy strict, pytest)

### US-002: Steam community resolver
**Description:** As the owner, I want a small, safe client that turns a vanity name into a SteamID64 without an API key.

**Acceptance Criteria:**
- [ ] `cs2/models.py` adds `VanityNotFoundError(Cs2Error)` ("No Steam profile uses that custom URL. Check the spelling, or use your 17-digit SteamID64.") and `SteamLookupError(Cs2Error)` ("Steam couldn't be reached to look up that name. Try again later, or use your 17-digit SteamID64 instead.")
- [ ] New module `cs2/resolver.py` defines a `SteamResolver` Protocol (`async resolve_vanity(name: str) -> str`, `async aclose() -> None`) and `SteamCommunityResolver`, the only module that talks to `steamcommunity.com`
- [ ] `SteamCommunityResolver` requests exactly `https://steamcommunity.com/id/<quoted name>/?xml=1`, where the name is re-validated against the vanity regex from US-001 and URL-quoted (a name that fails validation raises `InvalidSteamIdError` without any request); the host, scheme and path prefix are constants
- [ ] The HTTP call is injectable like `LeetifyClient` (`fetch(url, headers) -> FetchResponse`, reusing `FetchFn` / `FetchResponse` from `cs2/client.py`, or moving them to a shared module without changing `client.py` behaviour); the default uses a lazily created `aiohttp.ClientSession` with `allow_redirects=False`, a 10 second timeout, and reads at most 64 KiB of the body
- [ ] The response is **not** parsed with an XML library. The ID is taken with a regex such as `<steamID64>(7656119\d{10})</steamID64>` (`re.ASCII`); the first match wins
- [ ] Mapping: ID found -> return it; a 200 body containing an `<error>` element or no ID -> `VanityNotFoundError`; 404 -> `VanityNotFoundError`; 429 -> `RateLimitedError`; redirects (3xx), other non-2xx, timeouts, `aiohttp.ClientError`, `OSError` and `UnicodeDecodeError` -> `SteamLookupError`; warnings are logged without tracebacks and without the response body
- [ ] `aclose()` closes the session and is safe to call twice or when no session was created
- [ ] A fixture `tests/fixtures/steam/profile.xml` (trimmed real shape: `<profile>` with `steamID64`, `steamID`, `privacyState`) and `profile_not_found.xml` (`<response><error><![CDATA[The specified profile could not be found.]]></error></response>`) are used by the tests
- [ ] Unit tests with a stub `fetch`: public and private profile bodies, not-found body, 404, 429, 302, 500, timeout, `ClientError`, `UnicodeDecodeError`, an oversized body that still contains an ID after the cap (must not be found), a body with two IDs (first wins), the exact URL requested, an invalid name making zero requests, and `aclose` idempotence
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-003: Use the resolver in `/link`
**Description:** As a user, I want `/link` to resolve my custom URL and link my account in one step.

**Acceptance Criteria:**
- [ ] `Cs2` takes a `SteamResolver` by injection; `setup` builds a `SteamCommunityResolver`, and `cog_unload` closes it as well as the stats provider
- [ ] `handle_link` flow: parse (invalid input is answered immediately and does not defer or take the cooldown); for a `SteamId` the flow is unchanged (already-linked check, cooldown, defer, profile lookup, save), with **zero** resolver calls
- [ ] For a `VanityName`: take the `/link` cooldown, defer, resolve to a SteamID64, then check the stored link (same ID -> the existing "already linked" message through the followup), then look up the Leetify profile and save, as for an ID
- [ ] The cooldown applies to vanity lookups exactly as for any API-backed `/link` (taken before the first network call, not refunded afterwards)
- [ ] `VanityNotFoundError`, `SteamLookupError`, `RateLimitedError` and other `Cs2Error`s from the resolver are sent ephemerally through `_send_safely` with their `message`; unexpected exceptions are logged and answered with the generic message
- [ ] The success reply names the account as before (the Leetify name), and when the input was a vanity name it also shows the resolved SteamID64 so the user can check it is theirs (for example `Linked to **Name** on Leetify (Steam ID 7656119...)`)
- [ ] Unit tests with a fake resolver, fake provider and fake store: vanity URL and bare name link successfully, SteamID64 and `/profiles/` input make no resolver call, vanity resolves to the already linked ID, resolver errors of each type, a vanity name that resolves but has no Leetify profile (`PlayerNotFoundError` message), the cooldown is taken once and not refunded, defer happens before the resolver call (assert order), and `cog_unload` closes both objects
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-004: Documentation
**Description:** As the owner, I want the docs to say what `/link` accepts and what the bot needs to reach.

**Acceptance Criteria:**
- [ ] README `/link` text lists the accepted inputs (SteamID64, `/profiles/<id>` URL, `/id/<name>` URL, bare custom URL name), says vanity names are resolved through Steam's public profile page without an API key, and adds manual checks: link by custom URL, by bare name, by SteamID64, a non-existent name, and relinking the same account by name ("already linked")
- [ ] `docs/deployment.md` states that the bot needs outbound HTTPS to `steamcommunity.com` as well as the Leetify API host, and its troubleshooting table gains a row for "Steam couldn't be reached" / vanity lookups failing (network, Steam outage, rate limiting; workaround: use the 17-digit ID)
- [ ] `CLAUDE.md` architecture list gains `cs2/resolver.py` and mentions `parse_steam_target` in `steam.py`
- [ ] The old "vanity URLs aren't supported" wording is gone from README, docs and error messages
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

## Functional Requirements

- FR-1: `/link` accepts a SteamID64, a `/profiles/<id>` URL, a `/id/<name>` URL, or a bare custom URL name.
- FR-2: SteamID64 and `/profiles/` input never trigger a Steam request.
- FR-3: A vanity name is validated (2-32 chars, `A-Za-z0-9_-`) before it is placed in a URL; only `https://steamcommunity.com/id/<name>/?xml=1` is ever requested.
- FR-4: Redirects are not followed; the response body read is capped at 64 KiB; the response is matched with a regex and never parsed as XML.
- FR-5: Every failure (not found, rate limit, network, unexpected body) is a friendly ephemeral message that suggests the 17-digit ID where useful.
- FR-6: Vanity lookups share `/link`'s 15-second cooldown and go through the deferred followup path.
- FR-7: No new setting, API key or dependency.

## Non-Goals

- No Steam Web API (`ResolveVanityURL`) or Steam API key support.
- No caching of resolved names (the cooldown limits traffic; the stored link is the cache).
- No search by display name or Steam friend codes.
- No change to how links are stored or to `/unlink`.
- No change to deployment or prod configuration beyond the documentation.

## Technical Considerations

- The XML endpoint is public but unofficial; Steam may change or rate limit it. That is why failures degrade to the 17-digit ID and why there is no hard dependency on it.
- Reusing `FetchFn` / `FetchResponse` from `client.py` keeps one shape for injectable HTTP; moving them to a small shared module is fine if it avoids an import cycle.
- A Steam profile that is private still returns its `steamID64` in the XML (to be confirmed in the manual check); Leetify may then report the profile as private or unknown, which `/link` already reports.
- Vanity names are case-insensitive on Steam, so the resolved SteamID64 (not the typed name) is what gets stored.
- The cooldown, defer and `_send_safely` behaviour from the hardening PRD must not regress (the existing `/link` tests cover it).

## Success Metrics

- `./scripts/check.sh` passes on the branch after every story.
- Manually, in the dev test server: `/link` with a `/id/<name>` URL and with a bare name link the same account as the SteamID64 does, and a made-up name gives the "No Steam profile uses that custom URL" reply.

## Open Questions

- Does Steam redirect `/id/<name>/?xml=1` for any profiles (for example renamed custom URLs)? If so, a follow-up could allow one same-host redirect. Redirects are refused for now.
- Should a stored link remember how it was entered (vanity name vs ID) to show it in a future `/whoami`? Not needed today.
