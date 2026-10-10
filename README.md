# ninja-discord-bot

Personal Discord bot for a private server. See CLAUDE.md for architecture and conventions.

## Local setup
1. Create a dev bot + private test server in the Discord Developer Portal.
2. `cp .env.example .env` and fill in `DISCORD_TOKEN` (dev bot) and `GUILD_ID` (test server).
3. `uv sync`
4. `./scripts/check.sh` then `uv run python -m ninja_bot`

## /download
`/download url:<link> format:<mp3|mp4>` fetches a video with yt-dlp and posts the file plus an info embed. `format` defaults to mp4. The bot picks the best quality that fits the server's upload limit (10 MiB without a guild), rejects live streams, playlists and videos longer than `MAX_DURATION_SECONDS` (default 3600). One download runs at a time; a request waiting behind another download shows a "queued" message and gives up with a "bot is busy" reply after 5 minutes. Each user has a 30-second cooldown that only starts after a valid request (an invalid URL or clip doesn't count, and a failed download can be retried right away). mp4 requests are always remuxed to mp4, and only quality levels the video actually has are tried.

**Clips:** add `start` (and optionally `end`) to post only part of the video, e.g. `/download url:<link> format:mp3 start:1:30 end:2:45`. Accepted time formats: plain seconds (`90`, `90.5`), `m:ss` / `h:mm:ss` (with optional tenths, e.g. `1:30.5`), and units in h/m/s order (`1h2m3s`, `2m`, `45s`, `1m30.5s`). `start` alone clips to the end of the video; `end` without `start` is rejected, as is an `end` at or before `start` or a `start` past the end of the video. An `end` beyond the video is clamped to its length. Clips must be at least 1 second long. `MAX_DURATION_SECONDS` and the upload size limit apply to the clip length, so you can clip a section from a video longer than the cap. Clips skip the whole-video size filter when picking a format (yt-dlp's size is for the full video); the clip's real file size is checked after download.

**System dependencies:** ffmpeg (audio extraction and video merging) and **deno** (JavaScript runtime yt-dlp needs for YouTube; without it downloads fail with `HTTP Error 403`). Both are in `Dockerfile.dev`; deno is pinned to **2.9.7** (`denoland/deno:bin-2.9.7`, verified with yt-dlp 2026.8.19). Bump the deno tag deliberately when updating yt-dlp, and re-check YouTube downloads. On the Debian 13 prod LXC run `apt install ffmpeg unzip curl` and install deno (`curl -fsSL https://deno.land/install.sh | sh`, then put `~/.deno/bin` on the service's PATH). yt-dlp is installed with its `[default]` extra, which provides `yt-dlp-ejs`.

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
Generate `scripts/ralph/prd.json` (via the `ralph-skills:prd` and `ralph-skills:ralph` skills), then run `scripts/ralph/ralph.sh 10`. It refuses to run unless `.env` has `ENVIRONMENT=dev`.
