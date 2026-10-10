# ninja-discord-bot

Personal Discord bot for a private server. See CLAUDE.md for architecture and conventions.

## Local setup
1. Create a dev bot + private test server in the Discord Developer Portal.
2. `cp .env.example .env` and fill in `DISCORD_TOKEN` (dev bot) and `GUILD_ID` (test server).
3. `uv sync`
4. `./scripts/check.sh` then `uv run python -m ninja_bot`

## /download
`/download url:<link> format:<mp3|mp4>` fetches a video with yt-dlp and posts the file plus an info embed. `format` defaults to mp4. The bot picks the best quality that fits the server's upload limit (10 MiB without a guild), rejects live streams, playlists and videos longer than `MAX_DURATION_SECONDS` (default 3600). One download runs at a time, with a 30-second per-user cooldown.

**System dependency:** ffmpeg (audio extraction and video merging). It is in `Dockerfile.dev`; on the Debian 13 prod LXC run `apt install ffmpeg`.

**yt-dlp upkeep:** sites change often. When extractors break, run `uv lock --upgrade-package yt-dlp`, then `uv sync`, `./scripts/check.sh`, and redeploy.

**Manual test checklist** (dev bot in the test server):
- [ ] Short video as mp3 → audio file + embed
- [ ] Short video as mp4 (default) → video file + embed
- [ ] Video too large for the upload limit → friendly "too large" reply with embed
- [ ] Bogus URL (e.g. `not a url`) → ephemeral invalid-URL reply
- [ ] Private/unavailable video → friendly download error
- [ ] Playlist link → "unsupported" reply (a video link with `&list=` should download just that video)

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
