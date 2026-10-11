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
   - Leave the **Privileged Gateway Intents** off, except **Message Content Intent** when you set `TOASTY_BOT_ID` (see below).
4. Optionally set the name, avatar and banner. Prod and dev should look different so you never mix them up.

### Intents

The bot uses `discord.Intents.default()`, which only needs the non-privileged intents. It never reads member lists or presences. It reads message content only when `TOASTY_BOT_ID` is set: then it requests the privileged **Message Content** intent (to read Toasty's replies to `;pokemon`), and you must turn on **Message Content Intent** on the *Bot* tab of that application, or Discord refuses the connection at startup (`PrivilegedIntentsRequired`). Without `TOASTY_BOT_ID`, **no privileged intent has to be enabled**. `/scoreboard` shows linked friends by their server name by looking each one up over REST (`fetch_member`, at most ten per scoreboard), which needs neither the Server Members intent nor an extra permission. If you ever add a feature that needs another one, enable it in the portal *and* add it in [bot.py](../src/ninja_bot/bot.py). Request only what a feature needs.

### Permissions

All commands are slash commands, which Discord delivers as interactions, and replies to interactions generally do not depend on the bot's channel permissions. Grant the following anyway so the bot behaves the same in every channel:

| Permission | Why |
|---|---|
| View Channels | See the channel it is used in |
| Send Messages | Post replies and any non-interaction messages |
| Embed Links | Show the info embeds |
| Attach Files | Upload downloaded audio and video |

Permission integer: **52224** (`1024 + 2048 + 16384 + 32768`). No administrator, no role or member management.

**Announcement channel (`CS2_CHANNEL_ID`):** automatic CS2 match posts are normal messages, not interaction replies, so here the channel permissions really matter. The bot needs **View Channel, Send Messages and Embed Links in the announcement channel** (Send Messages in Threads if it is a thread). The channel can be a text channel, a thread or a voice channel's text chat. Check the channel's permission overrides, not just the server role. Without them the bot logs one warning, `Not posting CS2 matches: the CS2_CHANNEL_ID channel is <cause>`, where the cause is `missing`, `not a text channel`, `missing View Channel, Send Messages or Embed Links` or `unavailable (Discord error)` (a temporary Discord failure; retried next cycle), and posts nothing. What the auto-posts do is described in the [README](../README.md#auto-posted-matches).

**Activity channel (`ACTIVITY_CHANNEL_ID`):** the monthly activity overview is a normal message too, with the same requirements: **View Channel, Send Messages and Embed Links** (Send Messages in Threads for a thread) in that channel. Otherwise the bot logs one warning, `Not posting the monthly activity overview: the ACTIVITY_CHANNEL_ID channel is <cause>` (same causes as above), and posts nothing until the channel works. See the [README](../README.md#monthly-auto-post).

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

Requirements: [uv](https://docs.astral.sh/uv/), Python 3.12+ (the repo pins 3.13 in `.python-version`, uv fetches it), ffmpeg and deno (see [system dependencies](#system-dependencies)). Or use the [dev container](../README.md#dev-container), which has everything.

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

Suggested container: unprivileged Debian 13, 1 vCPU, 1 GiB RAM, 8 GiB disk with a few GiB free (downloads and remuxes use temp space), network access to Discord and the video sites, plus outbound HTTPS to the Leetify API (`api-public.cs-prod.leetify.com`) and `steamcommunity.com` (custom URL lookups in `/link`). Nothing needs to listen on a port, the bot only makes outbound connections.

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
# Optional: auto-post new CS2 matches of linked players here.
# CS2_CHANNEL_ID=<channel id>
# CS2_POLL_SECONDS=300
# Optional: monthly activity report channel and its time zone.
# ACTIVITY_CHANNEL_ID=<channel id>
# ACTIVITY_TIMEZONE=Europe/Amsterdam
# Optional: track Toasty ;pokemon catches (enable Message Content Intent in the portal first).
# TOASTY_BOT_ID=<toasty user id>
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

The bot logs at INFO level to stderr, which journald keeps along with stdout. The bot finds `.env` through `python-dotenv`'s default search (`load_dotenv()` with no path), which works here because the install is editable and the file sits in the repo root; a variable already set in the unit (for example `Environment=`) wins over `.env`. The unit's `WorkingDirectory` still matters because the default `DATA_DIR` (`data`) is relative to it. `data/` is gitignored and survives `git pull`. It holds `cs2.sqlite3`, which contains the Discord-to-Steam **link list** (for `/scoreboard`, `/profile`, `/leaderboard` and auto-posts) and the **posted-match log** (which matches were already announced and which players were seeded). It must be writable by `ninja` and **backed up** (for example with Proxmox backups of the container); losing it means everyone has to `/link` again, and an old copy of it can make recent matches be posted again. It also holds `activity.sqlite3`: the recorded **voice sessions** (Discord has no voice history, so losing it loses the history for good), the log of which monthly activity overviews were already posted (an old copy can make a month's overview be posted again), and, with `TOASTY_BOT_ID`, the recorded Toasty `;pokemon` events (no message text). If you set `DATA_DIR`, the same applies to that directory. The tables and their meaning are described in the [README](../README.md#data-and-storage).

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

If a release adds new settings, add them to the prod `.env` **before** restarting, and check the [CHANGELOG](../CHANGELOG.md) and the README's [configuration](../README.md#configuration) for them.

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

Read from the environment (and `.env`; real environment variables win). See [config.py](../src/ninja_bot/config.py). The [README](../README.md#configuration) has the full reference with defaults; this table is the dev/prod view.

| Variable | Dev | Prod | Meaning |
|---|---|---|---|
| `ENVIRONMENT` | `dev` | `prod` | Defaults to `dev` when unset, so set `prod` explicitly. Ralph, `smoke.py` and `capture_leetify.py` only run when this is `dev` |
| `DISCORD_TOKEN` | dev token | prod token | Required. The bot token from step 1 |
| `GUILD_ID` | test server ID | unset | Required in dev. When set, commands sync to that server only |
| `MAX_DURATION_SECONDS` | optional | optional | Longest video/clip `/download` accepts. Default `3600` |
| `LEETIFY_API_KEY` | optional | recommended | Leetify public API key for `/link`, `/scoreboard`, `/profile` ([leetify.com/app/developer](https://leetify.com/app/developer)). Empty = requests go unauthenticated |
| `CS2_CHANNEL_ID` | optional | optional | Channel for automatic CS2 match posts (new matches of linked players). Unset = no auto-posts. The bot needs View Channel, Send Messages (Send Messages in Threads for a thread) and Embed Links there |
| `CS2_POLL_SECONDS` | optional | optional | How often (seconds) to check Leetify for new matches when `CS2_CHANNEL_ID` is set. Default `300`. Each cycle makes one Leetify request per linked player, so don't set it very low |
| `DATA_DIR` | optional | optional | Directory for persistent data (`cs2.sqlite3`: links and the posted-match log; `activity.sqlite3`: voice sessions, Toasty `;pokemon` events and posted monthly reports). Default `data` (relative to `WorkingDirectory`) |
| `ACTIVITY_CHANNEL_ID` | optional | optional | Channel for the automatic monthly activity report (previous month, posted once after the month ends in `ACTIVITY_TIMEZONE`). Unset = no monthly auto-post. The bot needs View Channel, Send Messages (Send Messages in Threads for a thread) and Embed Links there |
| `ACTIVITY_TIMEZONE` | optional | optional | IANA time zone name for activity month boundaries. Default `UTC`; an unknown name is a `ConfigError` at startup |
| `TOASTY_BOT_ID` | optional | optional | User ID of the Toasty bot. Set = track `;pokemon` catches and request the Message Content intent, which must also be enabled in the Developer Portal. Unset = no Toasty tracking |

## 6. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `ConfigError: DISCORD_TOKEN is not set` | `.env` missing or empty (the bot looks for it via `python-dotenv`'s default search, normally the repo root), or the variable is set but empty in the unit |
| `ConfigError: GUILD_ID is required when ENVIRONMENT=dev` | Set `GUILD_ID` in dev, or `ENVIRONMENT=prod` for prod. `ENVIRONMENT` defaults to `dev` when unset, so a prod box that forgot it fails with this error |
| Portal error "A private application cannot have a default authorization link" when saving the Bot tab | Installation tab, set **Install Link** to **None**, save, then turn **Public Bot** off again (step 1) |
| `LoginFailure: Improper token` | Wrong or reset token, or stray spaces or quotes. Reset the token in the portal and paste it again |
| `Forbidden: 50001 Missing Access` at startup | The bot is not in the server `GUILD_ID` points to, or the ID is wrong. Re-invite or fix the ID |
| Bot online but no slash commands | The invite lacked `applications.commands`. Re-invite with the URL above. In prod, wait a few minutes after the first start |
| Commands appear twice | Either the dev and prod bots are both in the same server (each registers its own `/download`), or one bot was started once with `GUILD_ID` and once without, so it has both guild and global copies. Keep dev in the test server only and always start dev with `GUILD_ID` |
| "The application did not respond" | The bot is offline or crashed. Check `systemctl status ninja-bot` and `journalctl -u ninja-bot` |
| `/download` fails with `HTTP Error 403` on YouTube | deno missing or not on the service's `PATH` (`/usr/local/bin`), or yt-dlp is outdated. Run `yt-dlp -v -F <url>` and look for `JS runtimes:` |
| `ffmpeg not found` | `apt install ffmpeg` in the container |
| `/link` by custom URL name says "Steam couldn't be reached" (logs: `Steam vanity lookup ...`) | The container can't reach `steamcommunity.com` over HTTPS, Steam is down, or Steam is rate limiting the lookups (the profile XML endpoint is unofficial). Check outbound access with `curl -sI https://steamcommunity.com`; meanwhile link with the 17-digit SteamID64, which skips the lookup |
| "Too large" for videos that should fit | The upload limit is the server's (10 MiB by default). A shorter clip, a lower quality, or a boosted server fixes it |
| Replies work but files are not attached | Check the bot's channel permission overrides for **Attach Files** and the server's upload limit |
| Auto-posts: nothing is posted | Check in order: `CS2_CHANNEL_ID` is set in `.env` and is the right channel ID (Developer Mode, right-click the channel, **Copy Channel ID**), then restart; the bot has View Channel, Send Messages and Embed Links in that channel (the log says `Not posting CS2 matches: the CS2_CHANNEL_ID channel is ...`); the player's Leetify profile is public (private profiles are skipped); Leetify has the match (new matches can take a while to show up there, and matches older than 3 hours are never posted). A newly linked player's existing matches are deliberately not posted, only matches after the link |
| Auto-posts: a match is not posted for a while | The full match lookup on Leetify keeps failing, or Leetify is rate limiting the bot (logs: `Skipping match ... in the match poller` or `Leetify rate limited ...`). The bot never posts a partial scoreboard: it retries the match every cycle until it is posted or passes the 3-hour age limit, after which it is dropped. Nothing to fix unless it persists; check outbound access to Leetify |
| Auto-posts: old matches reposted | The posted-match log in `cs2.sqlite3` lost entries: the file was deleted or swapped for an old backup, or `DATA_DIR`/`WorkingDirectory` changed and the bot opened a different file. An old copy is the risky case: players count as seeded but recent posts are missing, so matches from the last 3 hours can be posted again (older ones never are). A deleted file loses all links too, so after everyone `/link`s again they are seeded silently and nothing old is posted. Keep `data/` persistent and backed up, and restore the newest backup |
| `PrivilegedIntentsRequired` at startup | `TOASTY_BOT_ID` is set, so the bot requests the Message Content intent, but **Message Content Intent** is off on the application's *Bot* tab. Turn it on (step 1), or unset `TOASTY_BOT_ID` |
| No monthly activity post | Check that `ACTIVITY_CHANNEL_ID` is set and is the right channel ID, then restart; the bot has View Channel, Send Messages and Embed Links there (the log says `Not posting the monthly activity overview: the ACTIVITY_CHANNEL_ID channel is ...`). The check runs hourly and right after start, and posts only after the month has ended in `ACTIVITY_TIMEZONE` and only months with voice time. A month in `reports_posted` is never posted again (it is recorded after every send attempt, even a failed one). If building the overview fails, the month is not recorded and the next hourly check retries it; the journal shows `Could not build the activity overview for <YYYY-MM>; retrying` with the traceback (`journalctl -u ninja-bot`). Behaviour: [README](../README.md#monthly-auto-post) |
| Toasty catches are not recorded | Check that `TOASTY_BOT_ID` is Toasty's user ID (Developer Mode, right-click Toasty, **Copy User ID**) and the bot restarted; that **Message Content Intent** is on in the Developer Portal (otherwise the bot does not start at all); that the bot can see the channel (**View Channel**); and that the member sent exactly `;pokemon` and Toasty replied to it. Errors while saving show as `Failed to record a Toasty reply` in the logs. Behaviour: [README](../README.md#toasty-pokemon-catches) |
| Disk fills up | Temporary download folders are removed after each request. If the bot is killed mid-download, a leftover `ninja-dl-*` folder can remain; with `PrivateTmp=true` it is cleared when the service stops or restarts |

## 7. Security checklist

- [ ] Prod token exists only in the prod `.env` (mode `600`, owner `ninja`)
- [ ] Dev and prod are separate applications with separate tokens
- [ ] **Public Bot** is off on both applications
- [ ] No Administrator permission, and no privileged intents except Message Content when (and only when) `TOASTY_BOT_ID` is set
- [ ] `.env` is not in git (it is gitignored) and has never been pasted into chat, issues or logs
- [ ] The service runs as the unprivileged `ninja` user
