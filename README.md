# ninja-discord-bot

Personal Discord bot for a private server. See CLAUDE.md for architecture and conventions.

## Setup and deployment
Full walkthrough in **[docs/deployment.md](docs/deployment.md)**: creating the Discord applications, required permissions and intents (no privileged intents; permission integer `52224`), the invite link, local dev, deploying to the Debian 13 LXC with systemd, updating, and troubleshooting.

## Local setup (quick version)
1. Create a dev bot + private test server in the Discord Developer Portal and invite the bot ([details](docs/deployment.md#1-create-a-discord-application)).
2. `cp .env.example .env` and fill in `DISCORD_TOKEN` (dev bot) and `GUILD_ID` (test server).
3. `uv sync`
4. `./scripts/check.sh` then `uv run python -m ninja_bot`

## /download
`/download url:<link> format:<mp3|mp4>` fetches a video with yt-dlp and posts the file plus an info embed. `format` defaults to mp4. The bot picks the best quality that fits the server's upload limit (10 MiB without a guild), rejects live streams, playlists and videos longer than `MAX_DURATION_SECONDS` (default 3600). One download runs at a time; a request waiting behind another download shows a "queued" message and gives up with a "bot is busy" reply after 5 minutes. Each user has a 30-second cooldown that only starts after a valid request (an invalid URL or clip doesn't count, and a failed download can be retried right away). mp4 requests are always remuxed to mp4, and only quality levels the video actually has are tried.

**Clips:** add `start` (and optionally `end`) to post only part of the video, e.g. `/download url:<link> format:mp3 start:1:30 end:2:45`. Accepted time formats: plain seconds (`90`, `90.5`), `m:ss` / `h:mm:ss` (with optional tenths, e.g. `1:30.5`), and units in h/m/s order (`1h2m3s`, `2m`, `45s`, `1m30.5s`). `start` alone clips to the end of the video; `end` without `start` is rejected, as is an `end` at or before `start` or a `start` past the end of the video. An `end` beyond the video is clamped to its length. Clips must be at least 1 second long. `MAX_DURATION_SECONDS` and the upload size limit apply to the clip length, so you can clip a section from a video longer than the cap. Clips skip the whole-video size filter when picking a format (yt-dlp's size is for the full video); the clip's real file size is checked after download.

**System dependencies:** ffmpeg (audio extraction and video merging) and **deno** (JavaScript runtime yt-dlp needs for YouTube; without it downloads fail with `HTTP Error 403`). Both are in `Dockerfile.dev`; deno is pinned to **2.9.7** (`denoland/deno:bin-2.9.7`, verified with yt-dlp 2026.8.19). Bump the deno tag deliberately when updating yt-dlp, and re-check YouTube downloads. Prod LXC install steps (ffmpeg, pinned deno, systemd) are in [docs/deployment.md](docs/deployment.md#3-production-debian-13-lxc-on-proxmox). yt-dlp is installed with its `[default]` extra, which provides `yt-dlp-ejs`.

**yt-dlp upkeep:** sites change often. When extractors break, run `uv lock --upgrade-package yt-dlp`, then `uv sync`, `./scripts/check.sh`, and redeploy.

**Manual test checklist** (dev bot in the test server):
- [ ] Short video as mp3 → audio file + embed
- [ ] Short video as mp4 (default) → video file + embed
- [ ] Video too large for the upload limit → friendly "too large" reply with embed
- [ ] Bogus URL (e.g. `not a url`) → ephemeral invalid-URL reply
- [ ] Private/unavailable video → friendly download error
- [ ] Playlist link → "unsupported" reply (a video link with `&list=` should download just that video)
- [ ] mp3 clip (`start` + `end`) → audio of just that section, embed shows the Clip field
- [ ] mp4 clip (`start` + `end`) → video of just that section, cut at the right times
- [ ] Start-only clip → from `start` to the end of the video
- [ ] `end` without `start` → ephemeral "give a start time too" reply
- [ ] `start` beyond the video length → friendly error mentioning the video length
- [ ] Short clip from a video longer than `MAX_DURATION_SECONDS` → downloads fine

## /scoreboard (CS2)
CS2 stats from the [Leetify](https://leetify.com) public API:
- `/link steam:<id, link or name>` links your Discord account to a Steam account. It accepts your SteamID64 (17 digits), a `steamcommunity.com/profiles/<id>` URL, a `steamcommunity.com/id/<name>` URL, or just your custom URL name. Custom URL names are resolved to a SteamID64 through Steam's public profile page (no API key needed); the reply then shows the resolved ID so you can check it is yours. `/unlink` removes the link. Both replies are only visible to you.
- `/scoreboard [player] [match]` posts the full scoreboard of your (or `player`'s) latest match, or of a specific Leetify match id: both team tables, fun awards, the focus player's stats, Leetify breakdown and recent form.
  - Every player in the match is listed: if Leetify's match history only returns your own entry, the bot fetches the full match by id, and players without a team number go into an `Other players` table.
  - Friends who `/link`ed their Steam account and are members of this server show up by their Discord name in the tables, and in a `Friends in this match` line as an `@mention` next to their Steam name. Mentions never ping anyone.
  - Detailed stats, the Leetify breakdown and recent form are only shown for the focus player (you, or `player`), not for every friend in the match.
  - No extra Discord intent or permission is needed: members are looked up through the REST API (`fetch_member`), not the privileged Server Members intent.
- `/profile [player]` posts ranks, winrate, Leetify rating bars and recent form.

`/scoreboard` and `/profile` share a 10-second per-user cooldown; it is refunded when the lookup fails. `/link` has its own 15-second cooldown that only applies after a valid request (an invalid Steam ID doesn't count) and is not refunded; linking the account you're already linked to by SteamID64 or `/profiles/` URL just says so, without a Leetify lookup or cooldown (by custom URL name, the name has to be looked up first, so the cooldown applies). A wrong `match:` id gets a "match not found" reply instead of the "player not found" one. Player names from Leetify are escaped, so markdown in a name is shown as plain text. Players need a public Leetify profile.

**Settings** (see [`.env.example`](.env.example) and the [settings reference](docs/deployment.md#5-settings-reference)):
- `LEETIFY_API_KEY`: get one at [leetify.com/app/developer](https://leetify.com/app/developer). Optional in config (without it requests are sent unauthenticated), but recommended.
- `CS2_CHANNEL_ID`: channel for [automatic match posts](#auto-posted-matches-cs2). Unset = no auto-posts.
- `CS2_POLL_SECONDS` (default 300): how often to check Leetify for new matches when `CS2_CHANNEL_ID` is set.
- `DATA_DIR` (default `data`): links and the log of already posted matches are stored in `<DATA_DIR>/cs2.sqlite3`. In prod this directory must be writable by the service user and persistent (and backed up), or everyone has to `/link` again and old matches can be posted again.

**Checking the test fixtures:** the Leetify fixtures in `tests/fixtures/leetify/` are hand-written from the API docs. Run `uv run python scripts/capture_leetify.py <steam64_id>` (dev only, needs `LEETIFY_API_KEY`) to save the real profile, matches list and latest match, with Steam IDs and names replaced by placeholders, to `data/leetify-capture/` (gitignored), then compare field names and units with the fixtures.

**Manual test checklist** (dev bot in the test server):
- [ ] `/link` with a SteamID64, then with a profile URL → ephemeral confirmation
- [ ] `/link` with a custom URL (`steamcommunity.com/id/<name>`) → ephemeral confirmation showing the resolved Steam ID
- [ ] `/link` with a bare custom URL name → same confirmation
- [ ] `/link` with a custom URL name nobody uses → "No Steam profile uses that custom URL" reply
- [ ] `/link` by name for the account you're already linked to → "already linked" reply
- [ ] `/scoreboard` → embed of your latest match (team tables, awards, your stats, breakdown, form)
- [ ] `/scoreboard player:@friend` and `/scoreboard match:<id>` → that player's / that match's scoreboard
- [ ] `/scoreboard` → all ten players show up in the team tables (not just you)
- [ ] Match with two linked friends → their Discord names in the tables and a `Friends in this match` line with mentions that don't ping
- [ ] Linked friend who left the server → falls back to their Steam name, not in the friends line
- [ ] Player with no team in the Leetify data (if one turns up) → listed under `Other players`
- [ ] `/profile` and `/profile player:@friend` → ranks, winrate, rating bars, form
- [ ] Private Leetify profile → friendly "private profile" reply
- [ ] Unlinked user (`/scoreboard player:@someone-unlinked`) → "not linked" reply
- [ ] Mobile layout: team tables and rating bars stay readable on the Discord phone app
- [ ] `/unlink` twice → removed, then "You don't have a linked Steam account."
- [ ] `/link` twice quickly with different accounts → the second is throttled by the cooldown
- [ ] `/link` with the account you're already linked to → "already linked" reply
- [ ] `/scoreboard match:nonsense` → "match not found" reply
- [ ] Player name with markdown (e.g. `*bold*` or `[x](y)`) in a test match → rendered as plain text

## /leaderboard (CS2)
`/leaderboard [metric]` ranks every linked player in the server by one metric: Leetify rating (default), aim, positioning, utility, clutch, opening duels or winrate. Each line shows the player as a mention (it never pings), their value, and their trend: the average rating of their newest 5 matches against the 5 before (`↑ +0.04`, `↓ -0.02` or `→` within ±0.01; no trend with fewer than 3 rated matches in either window), plus a win or loss streak of 3 or more (`🔥3` / `🧊3`). The top 3 get medals, ties share a rank, and players without a value for the metric are listed last as `-`.

At most 25 linked players are checked. Private or unavailable Leetify profiles are skipped and counted in the footer (`Skipped: N (private or unavailable)`); if Leetify rate limits the bot, it posts what it has with a note. Profiles are cached for 5 minutes, so running it twice in a row doesn't hit Leetify again. `/leaderboard` has its own 30-second per-user cooldown, refunded when nobody is linked or every lookup failed.

## Auto-posted matches (CS2)
With `CS2_CHANNEL_ID` set, the bot checks Leetify every `CS2_POLL_SECONDS` (default 300) for new matches of linked players and posts a scoreboard for each one in that channel. Unset, nothing runs.
- **Silent seeding:** the first time the bot sees a linked player (after a new `/link`, or the first start with this feature), it records their current match history as already posted and announces nothing. Only matches played after that are posted.
- **One post per match:** a match with several linked friends is posted once. The embed highlights the squad (`▸` in the team tables and a `Squad` field with each friend's stats, mentions that never ping) and is coloured by the squad's result.
- **Posted matches are remembered** in `<DATA_DIR>/cs2.sqlite3` (the newest 500), so a restart never reposts them. A match is marked as posted even if sending it failed, so a broken post is not retried every cycle.
- **3-hour age limit:** matches that finished more than 3 hours ago are skipped (and marked as posted), so a bot that was offline for a while doesn't flood the channel.
- **At most 3 posts per cycle**, oldest first; the rest follow in the next cycles. When Leetify rate limits the bot, it backs off and catches up in a later cycle.
- Matches can take a while to appear on Leetify after they end, and players with a private Leetify profile are never posted.
- The bot needs View Channel, Send Messages (Send Messages in Threads for a thread) and Embed Links in that channel; otherwise it logs one warning and posts nothing. See [deployment](docs/deployment.md#permissions).

**Manual test checklist** (dev bot in the test server, with `CS2_CHANNEL_ID` set):
- [ ] `/leaderboard` with 2+ linked accounts → ranked list with values, trends and medals; try a few `metric` choices
- [ ] `/leaderboard` with a private Leetify profile linked → that player is missing and the footer says `Skipped: 1 (private or unavailable)`
- [ ] A linked player finishes a new match → one post in the channel within a poll interval or two (after Leetify has the match), with `▸` highlights and the `Squad` field, without pinging anyone
- [ ] Restart the bot after a post → the same match is not posted again
- [ ] Match with two linked friends → posted once, both in the `Squad` field
- [ ] `/link` a new account that has older matches → none of its old matches are posted

## Dev container (recommended for Ralph)
Needs Docker or Podman (`sudo dnf install podman podman-compose`, or Docker CE).
```
docker compose run --rm dev                        # podman: PODMAN_USERNS=keep-id podman compose run --rm dev
claude                                             # first time only: log in (persisted in a volume)
uv run python scripts/smoke.py                     # inside the container
scripts/ralph/ralph.sh 10
```
The repo is bind-mounted, so commits land in your working tree. The container sees only the dev `.env`.

## Ralph
Generate `scripts/ralph/prd.json` (via the `ralph-skills:prd` and `ralph-skills:ralph` skills), then run `scripts/ralph/ralph.sh 10`. It refuses to run unless `.env` has `ENVIRONMENT=dev`. The full feature workflow (plan, PRD, Ralph branch, review, PR) is in [CLAUDE.md](CLAUDE.md#feature-workflow).
