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

## /help

- [ ] `/help` shows the overview privately: getting started plus Media, CS2 stats and Utility groups, and a footer hint
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
