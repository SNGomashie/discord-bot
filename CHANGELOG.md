# Changelog

What shipped in each merged pull request of [SNGomashie/discord-bot](https://github.com/SNGomashie/discord-bot), newest first. The bot has no version numbers, so entries are grouped by PR and merge date. Behaviour is described in the [README](README.md); the PRD of each feature is in [`tasks/`](tasks/). Manual checks that were still open at merge time are collected in [docs/manual-checks.md](docs/manual-checks.md).

## 2026-10-12

### Session polls: a date picker for tabletop sessions (`2026.42.0`)
- New `/session_poll name days times [weeks] [start] [duration_hours] [min_players] [round_hours]`: the bot generates candidate slots (up to 25) from weekdays, times and a number of weeks and posts a poll with **Join**, **Vote**, **Leave** and **Close round** buttons. Votes are cast in a private form (**Works for me** / **Only if needed**; anything else is a no) and the poll message updates its counts and `Waiting for` list live. The buttons keep working after a restart.
- A round closes when every joined player has voted (at least two players, so the GM's own vote never closes it), when the GM or someone with Manage Events presses **Close round**, or when the optional `round_hours` deadline passes. A slot fits when enough players can attend (`min_players`, else everyone who joined) and the GM can too. Up to 3 fitting slots come with **Confirm** buttons; with no fit the poll opens the next round (the following weeks, same days and times) and pings only the players who haven't voted, for up to 6 rounds.
- Confirming creates a session and announces it with Discord timestamps. It gets reminders 24 h and 1 h before the start (reminders already past at confirmation are skipped), and players who haven't voted are nudged in the channel every 24 h, at most 3 times per round. This is the only feature that pings people; everything else keeps mentions off.
- New `/sessions` (open polls and upcoming sessions) and `/session_cancel` (autocompletes polls and sessions; GM or Manage Events), each with a per-user cooldown.
- Slots are entered in the new `SESSIONS_TIMEZONE` (default `ACTIVITY_TIMEZONE`); data goes to `<DATA_DIR>/sessions.sqlite3`. No new intents.
- README, `.env.example`, `docs/deployment.md`, `docs/manual-checks.md`, `/help` (new Sessions group) and CLAUDE.md updated. 2165 tests in total.
- Review fixes shipped in the same PR: a lone GM's vote no longer closes a round; joins, leaves and votes are serialised with closing and confirming (no vote lands after evaluation, a double-clicked Join no longer reports "full"); automatic closes are re-checked under the lock; an empty Save is refused instead of counting as a no for everything; a late refresh can't bring buttons back on a closed message; a result or next-round message that could not be sent is retried every minute, and cancelling a poll also removes the Confirm buttons; the notify loop reads each poll's state once per cycle.
- Follow-ups: saved availability profiles that pre-fill votes (`prd-session-availability.md`, not written yet); a native Discord scheduled event on confirm; campaigns and recurring sessions; departed members are still counted and pinged; the GM is always required to attend.

## 2026-10-11

### Activity tracking: voice hours, monthly report and Toasty catches
- The bot now records voice time per member (the AFK channel is excluded). Open sessions are heartbeated every 60 seconds, so a restart or crash closes them at the last heartbeat and reopens the people still in voice. Data goes to `<DATA_DIR>/activity.sqlite3`.
- New `/activity [month]`: leaderboard with shared ranks, a wall of shame (members seen in voice within the last 90 days, including 0h, never members who left), awards (night owl, early bird, marathon, most consistent, biggest climber and drop for completed months) and social stats (most time together, busiest hour and weekday). New `/myactivity [user] [month]`: hours with rank, active days, streaks, longest and average session, a day strip and awards. Both reply publicly without pinging, with a 10-second per-user cooldown. A day is active from 5 minutes of voice time, and months follow `ACTIVITY_TIMEZONE`.
- With `ACTIVITY_CHANNEL_ID` set, the previous month's overview is posted automatically, once per month and restart-safe. A failed report build is retried the next hour; a failed send is not repeated.
- With `TOASTY_BOT_ID` set, `;pokemon` and Toasty's reply to it are recorded as a catch, a shiny catch or a cooldown hit, adding a Pokémon field (catches, shinies, most caught species) and an Impatient award. Only parsed fields are stored. This is the only feature that needs the privileged Message Content intent, requested only when `TOASTY_BOT_ID` is set.
- New settings `ACTIVITY_CHANNEL_ID`, `ACTIVITY_TIMEZONE` and `TOASTY_BOT_ID`; README, `.env.example`, `docs/deployment.md`, `docs/manual-checks.md`, `/help` (new Activity group) and CLAUDE.md updated. 1768 tests in total.
- Review fixes shipped in the same PR: wall of shame limited to recent, current members; the current streak survives a quiet morning; failed report builds are retried; months before 2015 are rejected with the month-format reply; the hourly report check no longer scales with the number of months; the CS2 and activity cogs share one auto-post channel helper (`posting.py`).
- Follow-ups: the raw form of Toasty's replies (mention format, embed or plain content, edits) is assumed from the Discord UI and still needs the manual check; streaks restart on the 1st; shame candidates could later be limited to a role.

### `/help` command (`2026.41.9`)
- New `/help`: with no argument it shows an overview (a "Getting started" block and every command grouped by Media, CS2 stats and Utility, with usage and a one-line summary). `/help command:<name>` shows one command's usage, parameters (required or optional, choices, accepted formats), examples and notes.
- The `command` option autocompletes from the commands the bot has registered. Names match case-insensitively and with an optional leading `/`; an unknown name gets a private reply with suggestions.
- Replies are private by default. `public:true` posts the help in the channel, with a 15-second per-user cooldown that applies only to public replies.
- Parameters, required flags and choices are read from the live command tree. All explanations and every other help string live in `src/ninja_bot/help/content.py`, so the wording can be changed in that one file.
- Drift tests fail `./scripts/check.sh` when a registered command or parameter has no help entry, when a help entry names something that doesn't exist, or when the README "Commands at a glance" table is out of step with the registered commands. 173 new tests (1382 in total).
- README, `docs/manual-checks.md` and CLAUDE.md updated; the "No help command" known limitation is gone.

### #9 Harden CS2 polling, Steam lookups and member names
Fixes from a code review of #6, #7 and #8. No new commands or options.
- A rate limit or API error during a full-match lookup could post an incomplete scoreboard and mark it done. Full-match lookups are now strict: a failed match is skipped for the cycle and retried later.
- Rate-limit errors were swallowed, so the backoff never ran and players later in the list were starved. A rate limit now returns the matches already completed, and the next cycle resumes from the player whose request was refused.
- A short network read of a Steam profile could report that a valid custom URL didn't exist. Responses are now read completely up to a 64 KiB cap, and profile and custom-URL links accept a trailing query or fragment.
- Discord member lookups are cached briefly (transient errors never). Former server members appear in the `Squad` field with their stats but without a mention.
- The leaderboard fetch policy moved into `cs2/squad.py` and the auto-post channel checks into `cs2/channels.py`.
- Follow-ups: bounded-concurrency fetching if more than about 8 people link; repeated full-match failures are retried until the 3-hour age limit with no attempt cap.

### #8 CS2 `/leaderboard`, form trends and auto-posted match scoreboards
- New `/leaderboard [metric]`: ranks linked players by Leetify rating (default), aim, positioning, utility, clutch, opening duels or winrate. Ties share a rank, players without a value go last, and each line shows a trend (newest 5 matches against the 5 before) and win or loss streaks of 3 or more. Up to 25 players, 30-second cooldown.
- Leetify profiles are cached for 5 minutes (errors never cached).
- Auto-posting: with `CS2_CHANNEL_ID` set, the bot polls every `CS2_POLL_SECONDS` (default 300) and posts a scoreboard for new matches of linked players. New players are seeded silently, matches older than 3 hours are dropped, at most 3 posts per cycle, and the newest 500 posted matches are remembered in `cs2.sqlite3`. A match with several linked friends is posted once, with squad highlights and a `Squad` field, coloured by the squad's result.
- Follow-ups: the age limit and per-cycle cap may become settings; the leaderboard doesn't check server membership.

### #7 Show every player and friends' Discord names in `/scoreboard`
- `/scoreboard` showed only the requester because the default path used the latest entry of Leetify's match history. If that entry lacks two non-empty teams, the bot now re-fetches the full match by id (falling back to the history entry on failure).
- Players with no team number, or on a third team, are listed in an `Other players` table instead of being dropped.
- Friends who linked their Steam account and are in the server appear by Discord display name, and in a `Friends in this match` field. Members are looked up over REST (no Server Members intent), at most 10 per scoreboard, and mentions never ping.
- Detailed stats, breakdown and form stay for the requester only.
- Follow-up: confirm against the real API (via `scripts/capture_leetify.py`) whether both endpoints return all players.

### #6 Accept Steam custom URLs and names in `/link`
- `/link` accepts a SteamID64, a `/profiles/` link, an `/id/` custom URL link, or a bare custom URL name (2 to 32 letters, digits, `_` or `-`).
- Names are resolved through Steam's public profile XML page with no API key. Redirects are refused, responses are capped, and any failure gives a friendly message pointing to the 17-digit ID. The reply shows the resolved ID.
- Follow-up: allow one same-host redirect for renamed custom URLs.

### #5 Harden the CS2 scoreboard feature
Ten findings from the review of #4. No new commands.
- `/link` now defers its reply before calling Leetify (it could exceed Discord's three-second window). It has its own 15-second cooldown that starts after valid input and isn't refunded. Linking the account you already have replies "already linked" without an API call.
- A wrong match id now says "match not found" instead of "player not found". Undecodable response bodies become friendly API errors.
- Player names are escaped wherever they appear outside code blocks, and team tables are sized by display width so CJK and emoji names stay aligned.
- `/scoreboard` fetches the match and the profile concurrently.

## 2026-10-10

### #4 CS2 scoreboard (`/link`, `/unlink`, `/scoreboard`, `/profile`) powered by Leetify
- New CS2 stats feature modelled on the Leetify bot, using the Leetify public API with no LLM calls.
- `/link` and `/unlink` manage a Steam link (stored in `<DATA_DIR>/cs2.sqlite3`). `/scoreboard [player] [match]` posts both team tables, rule-based awards, the player's breakdown and recent form. `/profile [player]` shows ranks, winrate, rating bars and form. `/scoreboard` and `/profile` share a per-user cooldown, refunded if nothing is posted.
- New settings: `LEETIFY_API_KEY` and `DATA_DIR`. `CS2_CHANNEL_ID` and `CS2_POLL_SECONDS` were added but did nothing until #8.
- Known gap: the test fixtures are hand-written from the API spec, so field names and units are unverified until `scripts/capture_leetify.py` is run with a real key. The production `data/` directory now needs backing up once linking is used.

### #3 Harden `/download` after code review
Nine review findings; no user-facing features.
- Clip requests skip the whole-video size filter, so short clips of long videos no longer fail with "format not available" (the real size is still checked after download).
- Overlong or non-ASCII `start` and `end` values give the friendly clip error instead of crashing. Clips resolve against the exact (fractional) video length.
- The mp4 quality ladder only contains heights the video has; webm and mkv sources are remuxed to mp4.
- The 30-second cooldown starts only after the URL and clip validate, and is refunded if nothing is posted.
- Queued requests show a "queued" message and time out after 5 minutes. Discord send errors are caught and logged. Blocking file work runs in worker threads.
- deno is pinned in `Dockerfile.dev` to match the verified yt-dlp version.

### #2 `start` and `end` options for `/download` (clips)
- Post just a section of a video. Accepted times: seconds, decimals, `m:ss`, `h:mm:ss` and unit forms (`2m`, `45s`, `1h2m3s`).
- `start` alone clips to the end; `end` without `start`, an `end` at or before `start`, a `start` past the video's end, and clips under 1 second are rejected. An `end` beyond the video is clamped.
- The duration cap and upload-size planning apply to the clip length. The embed gets a `Clip` field. Cuts are exact, so mp4 clips take longer.

### #1 `/download` command to post videos as mp3 or mp4
- New `/download url [format]`: fetches a single video with yt-dlp and posts the file with an embed (title, uploader, duration, views, upload date, thumbnail, format).
- Picks the best quality that fits the server's upload limit: mp3 from 192 down to 64 kbps, mp4 from 1080p down to 240p, with a real file-size check after download.
- Rejects playlists, live streams and videos longer than `MAX_DURATION_SECONDS` (default 3600). One download at a time and a 30-second per-user cooldown. Temp files are always cleaned up.
- New runtime dependencies: ffmpeg and deno (YouTube downloads fail with HTTP 403 without a JavaScript runtime), plus `yt-dlp[default]`.
