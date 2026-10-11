# Manual checks

Things only checkable inside Discord. They are not part of `./scripts/check.sh`. Run them with the **dev** bot in the test server (`uv run python scripts/smoke.py` first, then the commands). Behaviour is described in the [README](../README.md).

## /download

- [ ] Short video as mp3: audio file and embed
- [ ] Short video as mp4 (default): video file and embed
- [ ] Video too large for the upload limit: friendly "too large" reply with the embed
- [ ] Bogus URL (for example `not a url`): private invalid-URL reply
- [ ] Private or unavailable video: friendly download error
- [ ] Playlist link: "unsupported" reply (a video link with `&list=` downloads just that video)
- [ ] webm-only source as mp4: remuxed to a playable `.mp4`
- [ ] A second `/download` while one runs: the "queued" message appears, then the result
- [ ] mp3 clip (`start` + `end`): audio of just that section, embed shows the Clip field
- [ ] mp4 clip (`start` + `end`): video of just that section, cut at the right times
- [ ] Start-only clip: from `start` to the end of the video
- [ ] `end` without `start`: private "give a start time too" reply
- [ ] `start` beyond the video length: friendly error mentioning the video length
- [ ] Short clip from a video longer than `MAX_DURATION_SECONDS`: downloads fine
- [ ] Short clip of a long video against the 10 MiB limit: no "format not available" error
- [ ] Mistyped `end`, then retry right away: no cooldown reply
- [ ] Discord limits `start` and `end` to 32 characters

## /link, /unlink, /scoreboard, /profile

- [ ] `/link` with a SteamID64, then with a profile URL: private confirmation
- [ ] `/link` with a custom URL (`steamcommunity.com/id/<name>`): confirmation showing the resolved Steam ID
- [ ] `/link` with a bare custom URL name: same confirmation
- [ ] `/link` with a custom URL name nobody uses: "No Steam profile uses that custom URL" reply
- [ ] `/link` for the account you are already linked to (by ID, by name): "already linked" reply
- [ ] `/link` with a profile link carrying `?utm_source=x`: accepted
- [ ] `/link` with a custom URL whose Steam nickname is Cyrillic or CJK: links the right account
- [ ] `/link` twice quickly with different accounts: the second is throttled by the cooldown
- [ ] `/unlink` twice: removed, then "You don't have a linked Steam account."
- [ ] `/scoreboard`: embed of your latest match (team tables, awards, your stats, breakdown, form)
- [ ] `/scoreboard` run twice in a row: names and friends still show on the second run
- [ ] `/scoreboard`: all ten players are in the team tables, not just you
- [ ] `/scoreboard player:@friend` and `/scoreboard match:<id>`: that player's and that match's scoreboard
- [ ] `/scoreboard match:nonsense`: "match not found" reply
- [ ] Match with two linked friends: their Discord names in the tables, a `Friends in this match` line with mentions that don't ping
- [ ] Linked friend who left the server: Steam name instead, not in the friends line
- [ ] Player with no team in the Leetify data (if one turns up): listed under `Other players`
- [ ] Player name with markdown (for example `*bold*` or `[x](y)`): shown as plain text
- [ ] `/profile` and `/profile player:@friend`: ranks, winrate, rating bars, form
- [ ] Private Leetify profile: friendly "private profile" reply
- [ ] Unlinked user (`/scoreboard player:@someone-unlinked`): "not linked" reply
- [ ] Mobile layout: team tables and rating bars stay readable on the Discord phone app

## /leaderboard

- [ ] With 2+ linked accounts: ranked list with values, trends and medals; try several `metric` choices
- [ ] With a private Leetify profile linked: that player is missing and the footer says `Skipped: 1 (private or unavailable)`
- [ ] Mentions in the list don't ping anyone

## Auto-posts (with `CS2_CHANNEL_ID` set)

- [ ] A linked player finishes a new match: one post in the channel within a poll interval or two (once Leetify has the match), with `▸` highlights and the `Squad` field, without pinging anyone
- [ ] Restart the bot after a post: the same match is not posted again
- [ ] Match with two linked friends: posted once, both in the `Squad` field
- [ ] `/link` a new account that has older matches: none of its old matches are posted
- [ ] A linked friend who left the server finishes a match: the post lists their Steam name in the `Squad` field without a mention
- [ ] Failed full-match lookup: the post is delayed to a later cycle, not posted partial
- [ ] Wrong channel ID or missing Embed Links: one warning in the log, no crash
- [ ] Leetify rate limit: one skipped cycle (see the log), then posting resumes. If it can't be triggered, note it as unverifiable (covered by unit tests)

## Activity tracking

Check the `voice_sessions` table in `data/activity.sqlite3` after each step (for example `sqlite3 data/activity.sqlite3 'SELECT * FROM voice_sessions ORDER BY id DESC LIMIT 5'`).

- [ ] Join a voice channel: a new row with an empty `end_ts`; `last_seen` advances about once a minute
- [ ] Mute, deafen and start/stop a stream: no new row
- [ ] Move to another voice channel: the old row gets an `end_ts`, a new row opens for the new channel
- [ ] Move to the server's AFK channel: the row is closed and no new one opens; moving back opens a new one
- [ ] Leave voice: the row gets an `end_ts`
- [ ] Restart (or kill) the bot while in voice and wait a few minutes before starting it again: the old row ends at its last heartbeat (not at the restart time), and a new row opens for you on start
- [ ] A bot joining voice gets no row

### /activity and /myactivity

- [ ] `/activity` posts the current month publicly: leaderboard with mentions (nobody is pinged), wall of shame, awards, Together, footer `Times in <ACTIVITY_TIMEZONE>`
- [ ] `/activity month:2020-01` (no data) shows "Nobody was in voice in January 2020." and a second call right after is not throttled
- [ ] `/activity month:sept` and `/activity month:2099-01` give private errors
- [ ] `/myactivity` shows your month publicly (title is your server name, day strip, streaks); `/myactivity user:@friend month:<YYYY-MM>` shows theirs
- [ ] `/myactivity` for a member without voice time that month gives a private "wasn't in voice" reply
- [ ] A second `/activity` or `/myactivity` within 10 s gives a private "Slow down!" reply
- [ ] The embeds render cleanly on the phone app

### Monthly auto-post (with `ACTIVITY_CHANNEL_ID` set)

To force a post on the dev bot, give a past month some voice time and make sure it isn't marked posted, then restart (the check runs right after start):

```sh
sqlite3 data/activity.sqlite3 "INSERT INTO voice_sessions (user_id, channel_id, start_ts, end_ts, last_seen) VALUES (<your user id>, 1, strftime('%s','2026-01-10 20:00'), strftime('%s','2026-01-10 22:00'), strftime('%s','2026-01-10 22:00'))"
sqlite3 data/activity.sqlite3 "DELETE FROM reports_posted WHERE month_key >= '2026-01'"
```

The inserted session makes January the first month with data, so every ended month from January on that has voice time is posted, one per hour (oldest first). Delete the row again afterwards (`DELETE FROM voice_sessions WHERE channel_id = 1`).

- [ ] After the restart the overview for the oldest such month appears in the channel within a minute; nobody is pinged
- [ ] `SELECT * FROM reports_posted` now lists that month; another restart does not post it again
- [ ] Remove the bot's Send Messages (or Embed Links) in the channel and repeat: one warning `Not posting the monthly activity overview: the ACTIVITY_CHANNEL_ID channel is missing ...` in the log, nothing posted, and the month is not in `reports_posted`; restoring the permission posts it within an hour (or on restart)
- [ ] Without `ACTIVITY_CHANNEL_ID` nothing is posted

### Toasty `;pokemon` (with `TOASTY_BOT_ID` set)

Enable **Message Content Intent** for the dev application first. Check the rows with `sqlite3 data/activity.sqlite3 "SELECT * FROM pokemon_events ORDER BY ts DESC LIMIT 5"`.

- [ ] Send `;pokemon` and get a catch: a `catch` row with your user ID, the Pokémon name (without `Shiny `) and `shiny` 0
- [ ] A shiny catch (whenever one happens) gives `shiny` 1 and the name without `Shiny `
- [ ] `;pokemon` again during the cooldown: a `cooldown` row with an empty `pokemon`
- [ ] `; pokemon` (with a space) is recorded too; `;pokemon list` and someone else's message are not
- [ ] Note whether Toasty edits its reply or puts the text in an embed, and replace the `assumed` cases in `tests/fixtures/toasty/replies.json` with what you see
- [ ] Without `TOASTY_BOT_ID` (and the intent off in the portal) the bot starts and records nothing
- [ ] With `TOASTY_BOT_ID` set but **Message Content Intent** off in the portal, the bot stops at startup with `PrivilegedIntentsRequired`
- [ ] A Toasty reply to a `;pokemon` the bot didn't see (sent while it was offline) is not recorded
- [ ] After a few catches (one shiny if you're lucky) and 3 cooldown hits, `/activity` shows a **Pokémon** field: top catchers with medals (no pings), the shiny line, `🏆 Most caught`, `⏳ Impatient: @you (3 cooldown hits)` and the `🎯 Server` line; Impatient is not in the Awards field
- [ ] `/myactivity` shows `N catches · N shinies · N cooldown hits` and the Impatient award; a member who didn't play shows zeros
- [ ] A shiny can be faked with `sqlite3 data/activity.sqlite3 "INSERT INTO pokemon_events VALUES (1, <your user id>, strftime('%s','now'), 'catch', 'Pikachu', 1)"` if Toasty doesn't cooperate; delete the row afterwards

## /help

- [ ] `/help` shows the overview privately: getting started plus Media, CS2 stats, Voice activity and Utility groups, and a footer hint
- [ ] Typing `/help command:` offers all commands, narrows as you type (`do` -> `download`)
- [ ] `/help command:download` shows usage `/download url:<url> [format:mp3|mp4] [start:<start>] [end:<end>]`, parameter notes for clips and examples
- [ ] `/help command:/Link` (capital, leading slash) works the same as `link`
- [ ] `/help command:nonsense` gives a private "no command with that name" reply; `/help command:down` suggests `/download`
- [ ] `/help public:true` posts in the channel; a second public call within 15 s is throttled privately, a private call is not
- [ ] The embeds render cleanly on the phone app (no cut-off code blocks)
- [ ] `/help` works in a DM with the bot (global sync) and in the test server

## Leetify data

- [ ] Run `uv run python scripts/capture_leetify.py <steam64_id>` with a real key, read the `Result:` line, and compare field names and units in `data/leetify-capture/` with `tests/fixtures/leetify/` (the fixtures are hand-written from the API spec)
- [ ] Check the custom URL endpoint still returns the expected ID tag and doesn't redirect
