# ninja-discord-bot

Personal Discord bot for a private server. See CLAUDE.md for architecture and conventions.

## Local setup
1. Create a dev bot + private test server in the Discord Developer Portal.
2. `cp .env.example .env` and fill in `DISCORD_TOKEN` (dev bot) and `GUILD_ID` (test server).
3. `uv sync`
4. `./scripts/check.sh` then `uv run python -m ninja_bot`

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
