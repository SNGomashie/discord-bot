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
- `/link steam:<id or link>` links your Discord account to your SteamID64 (17 digits) or a `steamcommunity.com/profiles/<id>` URL. Vanity `/id/<name>` URLs are not accepted. `/unlink` removes the link. Both replies are only visible to you.
- `/scoreboard [player] [match]` posts the full scoreboard of your (or `player`'s) latest match, or of a specific Leetify match id: both team tables, fun awards, the focus player's stats, Leetify breakdown and recent form.
- `/profile [player]` posts ranks, winrate, Leetify rating bars and recent form.

`/scoreboard` and `/profile` share a 10-second per-user cooldown; it is refunded when the lookup fails. `/link` has its own 15-second cooldown that only applies after a valid request (an invalid Steam ID doesn't count) and is not refunded; linking the account you're already linked to just says so, without a Leetify lookup or cooldown. A wrong `match:` id gets a "match not found" reply instead of the "player not found" one. Player names from Leetify are escaped, so markdown in a name is shown as plain text. Players need a public Leetify profile.

**Settings** (see [`.env.example`](.env.example) and the [settings reference](docs/deployment.md#5-settings-reference)):
- `LEETIFY_API_KEY`: get one at [leetify.com/app/developer](https://leetify.com/app/developer). Optional in config (without it requests are sent unauthenticated), but recommended.
- `CS2_CHANNEL_ID` and `CS2_POLL_SECONDS` (default 300): reserved for automatically posting new matches; parsed and validated, but not used by any code yet.
- `DATA_DIR` (default `data`): links are stored in `<DATA_DIR>/cs2.sqlite3`. In prod this directory must be writable by the service user and persistent (and backed up), or everyone has to `/link` again.

**Checking the test fixtures:** the Leetify fixtures in `tests/fixtures/leetify/` are hand-written from the API docs. Run `uv run python scripts/capture_leetify.py <steam64_id>` (dev only, needs `LEETIFY_API_KEY`) to save the real profile, matches list and latest match, with Steam IDs and names replaced by placeholders, to `data/leetify-capture/` (gitignored), then compare field names and units with the fixtures.

**Manual test checklist** (dev bot in the test server):
- [ ] `/link` with a SteamID64, then with a profile URL → ephemeral confirmation; vanity URL → ephemeral hint
- [ ] `/scoreboard` → embed of your latest match (team tables, awards, your stats, breakdown, form)
- [ ] `/scoreboard player:@friend` and `/scoreboard match:<id>` → that player's / that match's scoreboard
- [ ] `/profile` and `/profile player:@friend` → ranks, winrate, rating bars, form
- [ ] Private Leetify profile → friendly "private profile" reply
- [ ] Unlinked user (`/scoreboard player:@someone-unlinked`) → "not linked" reply
- [ ] Mobile layout: team tables and rating bars stay readable on the Discord phone app
- [ ] `/unlink` twice → removed, then "You don't have a linked Steam account."
- [ ] `/link` twice quickly with different accounts → the second is throttled by the cooldown
- [ ] `/link` with the account you're already linked to → "already linked" reply
- [ ] `/scoreboard match:nonsense` → "match not found" reply
- [ ] Player name with markdown (e.g. `*bold*` or `[x](y)`) in a test match → rendered as plain text

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
