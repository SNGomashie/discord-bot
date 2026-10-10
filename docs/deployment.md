# Setup and deployment

How to create the Discord application, give it the right permissions, run it locally for development, and deploy it to the Proxmox LXC. Developer Portal menu names change now and then, so match on the wording rather than the exact position.

## Overview

You run **two separate bots** that never share a token:

| | Dev | Prod |
|---|---|---|
| Discord application | `Ninja (dev)` | `Ninja` |
| Lives in | a private test server | the friends' server |
| Runs on | your machine or the dev container | Debian 13 LXC on Proxmox |
| `ENVIRONMENT` | `dev` | `prod` |
| `GUILD_ID` | required (test server, commands sync instantly) | leave unset (global command sync) |
| Who touches it | you and Ralph | only you |

Agents and Ralph only ever get the **dev** token. Never commit `.env` or paste a token anywhere.

## 1. Create a Discord application

Do this once for dev and once for prod.

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**. Name it (`Ninja (dev)` / `Ninja`).
2. Note the **Application ID** on *General Information*. You need it for the invite link.
3. Open the **Bot** tab:
   - Click **Reset Token** and copy it right away. It is shown once. This is `DISCORD_TOKEN`. If it ever leaks, reset it here and update `.env`.
   - Turn **Public Bot** off so nobody else can add your bot to their servers. Discord refuses to save this while the app has a default install link, with the error *"A private application cannot have a default authorization link. Make sure the default authorization link is set to None on the Installation tab."* So first open the **Installation** tab, set **Install Link** to **None**, save, and then turn Public Bot off on the Bot tab. You then add the bot with the manual [invite link](#invite-link) below, which works for you as the server owner.
   - Leave **Requires OAuth2 Code Grant** off.
   - Leave all three **Privileged Gateway Intents** off (see below).
4. Optionally set the name, avatar and banner. Prod and dev should look different so you never mix them up.

### Intents

The bot uses `discord.Intents.default()`, which only needs the non-privileged intents. It does not read message content, member lists or presences, so **no privileged intent has to be enabled**. If you ever add a feature that needs one (for example Message Content), enable it in the portal *and* add it in [bot.py](../src/ninja_bot/bot.py). Request only what a feature needs.

### Permissions

All commands are slash commands, which Discord delivers as interactions, and replies to interactions generally do not depend on the bot's channel permissions. Grant the following anyway so the bot behaves the same in every channel and keeps working if a feature posts a normal message:

| Permission | Why |
|---|---|
| View Channels | See the channel it is used in |
| Send Messages | Post replies and any non-interaction messages |
| Embed Links | Show the info embeds |
| Attach Files | Upload downloaded audio and video |

Permission integer: **52224** (`1024 + 2048 + 16384 + 32768`). No administrator, no role or member management.

Notes:
- Users need **Use Application Commands** in the channel to see and run the commands. This is a user permission, not a bot one, and is on by default.
- The upload size limit comes from the server (`guild.filesize_limit`): 10 MiB by default, higher with server boosts. The bot picks the best quality that fits. A boosted server gives better quality downloads.

### Invite link

Replace `APP_ID` with the Application ID and open the URL while logged in as the server owner (or someone with *Manage Server*):

```
https://discord.com/oauth2/authorize?client_id=APP_ID&scope=bot%20applications.commands&permissions=52224
```

Both scopes are required:
- `bot` adds the bot user to the server.
- `applications.commands` lets the bot register slash commands. If it is missing the bot comes online but `/download` never shows up.

Invite `Ninja (dev)` only to the test server and `Ninja` only to the real one. Two bots that both register `/download` in the same server is confusing.

### Find the server ID (dev)

1. Discord, *User Settings*, *Advanced*, turn on **Developer Mode**.
2. Right-click the test server and choose **Copy Server ID**. This is `GUILD_ID`.

## 2. Local development

Requirements: [uv](https://docs.astral.sh/uv/), Python 3.12+ (the repo pins 3.13 in `.python-version`, uv fetches it), ffmpeg and deno (see [system dependencies](#system-dependencies)). Or use the dev container from the README, which has everything.

```
cp .env.example .env     # then fill in the dev values below
uv sync
./scripts/check.sh       # ruff, format, mypy, pytest
uv run python scripts/smoke.py   # logs in, syncs, lists the guild commands, exits
uv run python -m ninja_bot       # run the bot (Ctrl+C to stop)
```

Dev `.env`:

```
ENVIRONMENT=dev
DISCORD_TOKEN=<dev bot token>
GUILD_ID=<test server id>
```

In dev the commands are synced to `GUILD_ID` on every start, so changes show up immediately. The bot refuses to start if `GUILD_ID` is missing while `ENVIRONMENT=dev`. `smoke.py` also refuses to run outside dev.

## System dependencies

| Dependency | Needed for | Notes |
|---|---|---|
| ffmpeg | Audio extraction, merging, remuxing to mp4, clipping | Any recent Debian package |
| deno | yt-dlp's YouTube support (JS challenges) | Without it YouTube downloads fail with `HTTP Error 403`. Pinned to **2.9.7**, same as `Dockerfile.dev` |
| yt-dlp + `yt-dlp-ejs` | Everything under `/download` | Installed by `uv sync` through the `yt-dlp[default]` extra |

## 3. Production (Debian 13 LXC on Proxmox)

Suggested container: unprivileged Debian 13, 1 vCPU, 1 GiB RAM, 8 GiB disk with a few GiB free (downloads and remuxes use temp space), network access to Discord and the video sites. Nothing needs to listen on a port, the bot only makes outbound connections.

### 3.1 Packages and a service user

As root in the container:

```
apt update
apt install -y git ffmpeg unzip curl ca-certificates
useradd --system --create-home --home-dir /opt/ninja --shell /usr/sbin/nologin ninja
```

### 3.2 Install deno (system-wide, pinned)

```
curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s v2.9.7
deno --version        # should print 2.9.7
```

If the installer asks about editing shell profiles, decline: the binary only needs to be in `/usr/local/bin`. Keep this version in step with `Dockerfile.dev` (see [updating yt-dlp](#updating-yt-dlp)).

### 3.3 Install uv and the code

```
curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
sudo -u ninja git clone https://github.com/SNGomashie/discord-bot.git /opt/ninja/app
cd /opt/ninja/app
sudo -u ninja uv sync --frozen --no-dev
```

If the repository is private, give the container a read-only **deploy key** (GitHub, *Settings*, *Deploy keys*) and clone over SSH instead. `--frozen` installs exactly what `uv.lock` pins.

### 3.4 Configure

Create `/opt/ninja/app/.env`, owned by the service user and readable by nobody else:

```
install -m 600 -o ninja -g ninja /dev/null /opt/ninja/app/.env
nano /opt/ninja/app/.env
```

```
ENVIRONMENT=prod
DISCORD_TOKEN=<prod bot token>
# GUILD_ID intentionally unset in prod: commands sync globally.
MAX_DURATION_SECONDS=3600
LEETIFY_API_KEY=<leetify api key>
```

Use the **prod** token only here, never in the dev container or on your workstation.

Global commands can take a few minutes (occasionally longer) to appear after the first start or after a command changes. The bot re-syncs them itself on every start, so there is nothing to run by hand.

### 3.5 Run as a systemd service

`/etc/systemd/system/ninja-bot.service`:

```
[Unit]
Description=Ninja Discord bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ninja
Group=ninja
WorkingDirectory=/opt/ninja/app
ExecStart=/opt/ninja/app/.venv/bin/python -m ninja_bot
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```
systemctl daemon-reload
systemctl enable --now ninja-bot
systemctl status ninja-bot
journalctl -u ninja-bot -f       # live logs
```

The bot logs to stdout, so journald keeps the logs. The unit's `WorkingDirectory` matters: the bot loads `.env` from there and keeps persistent data in `data/` relative to it. `data/` is gitignored and survives `git pull`. It holds `cs2.sqlite3` (Discord-to-Steam links for `/scoreboard` and `/profile`), so it must be writable by `ninja` and **backed up** (for example with Proxmox backups of the container); losing it means everyone has to `/link` again. If you set `DATA_DIR`, the same applies to that directory.

### 3.6 Verify

1. `journalctl -u ninja-bot` shows `Logged in as ... (env=prod)`.
2. The bot shows as online in the server member list.
3. `/ping` replies with the latency.
4. `/download` works with a short YouTube video as mp3 and as mp4. If YouTube returns 403, check the runtime as the service user:
   ```
   sudo -u ninja /opt/ninja/app/.venv/bin/yt-dlp -v -F <url>      # look for "JS runtimes:"
   ```

## 4. Updating

Always change things in dev first, merge to `main` through a PR, then deploy.

```
cd /opt/ninja/app
sudo -u ninja git pull --ff-only
sudo -u ninja uv sync --frozen --no-dev
systemctl restart ninja-bot
journalctl -u ninja-bot -n 50
```

If a release adds new settings, add them to the prod `.env` **before** restarting, and check the release notes or README for them.

### Updating yt-dlp

Sites change often and extractors break. In dev:

```
uv lock --upgrade-package yt-dlp
uv sync
./scripts/check.sh
```

Merge it, then deploy as above. When yt-dlp or its EJS component asks for a newer deno, bump the tag in `Dockerfile.dev` and the version in step 3.2, reinstall deno on the LXC, and test a YouTube download.

### Rolling back

```
cd /opt/ninja/app
sudo -u ninja git log --oneline -5
sudo -u ninja git checkout <good-commit>
sudo -u ninja uv sync --frozen --no-dev
systemctl restart ninja-bot
```

Return to the branch with `git checkout main` once a fix is merged.

## 5. Settings reference

Read from the environment (and `.env`). See [config.py](../src/ninja_bot/config.py).

| Variable | Dev | Prod | Meaning |
|---|---|---|---|
| `ENVIRONMENT` | `dev` | `prod` | Ralph and `smoke.py` only run when this is `dev` |
| `DISCORD_TOKEN` | dev token | prod token | Required. The bot token from step 1 |
| `GUILD_ID` | test server ID | unset | Required in dev. When set, commands sync to that server only |
| `MAX_DURATION_SECONDS` | optional | optional | Longest video/clip `/download` accepts. Default `3600` |
| `LEETIFY_API_KEY` | optional | recommended | Leetify public API key for `/link`, `/scoreboard`, `/profile` ([leetify.com/app/developer](https://leetify.com/app/developer)). Empty = requests go unauthenticated |
| `CS2_CHANNEL_ID` | optional | optional | Reserved for automatic CS2 match posts; validated but not used yet |
| `CS2_POLL_SECONDS` | optional | optional | Reserved for automatic CS2 match posts (poll interval). Default `300`; not used yet |
| `DATA_DIR` | optional | optional | Directory for persistent data (`cs2.sqlite3`). Default `data` (relative to `WorkingDirectory`) |
| `TEST_CHANNEL_ID` | optional | unused | In `.env.example` but not read by any code yet |

## 6. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `ConfigError: DISCORD_TOKEN is not set` | `.env` missing, empty, or the service's `WorkingDirectory` is wrong |
| `ConfigError: GUILD_ID is required when ENVIRONMENT=dev` | Set `GUILD_ID` in dev, or `ENVIRONMENT=prod` for prod |
| Portal error "A private application cannot have a default authorization link" when saving the Bot tab | Installation tab, set **Install Link** to **None**, save, then turn **Public Bot** off again (step 1) |
| `LoginFailure: Improper token` | Wrong or reset token, or stray spaces or quotes. Reset the token in the portal and paste it again |
| `Forbidden: 50001 Missing Access` at startup | The bot is not in the server `GUILD_ID` points to, or the ID is wrong. Re-invite or fix the ID |
| Bot online but no slash commands | The invite lacked `applications.commands`. Re-invite with the URL above. In prod, wait a few minutes after the first start |
| Commands appear twice | Either the dev and prod bots are both in the same server (each registers its own `/download`), or one bot was started once with `GUILD_ID` and once without, so it has both guild and global copies. Keep dev in the test server only and always start dev with `GUILD_ID` |
| "The application did not respond" | The bot is offline or crashed. Check `systemctl status ninja-bot` and `journalctl -u ninja-bot` |
| `/download` fails with `HTTP Error 403` on YouTube | deno missing or not on the service's `PATH` (`/usr/local/bin`), or yt-dlp is outdated. Run `yt-dlp -v -F <url>` and look for `JS runtimes:` |
| `ffmpeg not found` | `apt install ffmpeg` in the container |
| "Too large" for videos that should fit | The upload limit is the server's (10 MiB by default). A shorter clip, a lower quality, or a boosted server fixes it |
| Replies work but files are not attached | Check the bot's channel permission overrides for **Attach Files** and the server's upload limit |
| Disk fills up | Temporary download folders are removed after each request. If the bot is killed mid-download, a leftover `ninja-dl-*` folder can remain; with `PrivateTmp=true` it is cleared when the service stops or restarts |

## 7. Security checklist

- [ ] Prod token exists only in the prod `.env` (mode `600`, owner `ninja`)
- [ ] Dev and prod are separate applications with separate tokens
- [ ] **Public Bot** is off on both applications
- [ ] No privileged intents and no Administrator permission
- [ ] `.env` is not in git (it is gitignored) and has never been pasted into chat, issues or logs
- [ ] The service runs as the unprivileged `ninja` user
