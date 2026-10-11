# ninja-discord-bot

A personal Discord bot for one private server of friends. It is built on discord.py 2.x (Python 3.12+), runs in a Proxmox LXC on a homelab, and makes no LLM calls. Everything it does is rule-based.

It does five things:

- **Media**: `/download` fetches a video (or a clip of it) with yt-dlp and posts it as an mp3 or mp4.
- **CS2 stats**: `/link`, `/unlink`, `/scoreboard`, `/profile` and `/leaderboard` show match scoreboards, profiles and a squad ranking from the [Leetify](https://leetify.com) public API.
- **CS2 auto-posts**: when a linked friend finishes a match, the bot posts the scoreboard in a channel by itself.
- **Voice activity**: the bot tracks time in voice channels (AFK excluded); `/activity` and `/myactivity` show a monthly leaderboard, wall of shame, awards and per-member stats (plus Toasty `;pokemon` catches when enabled).
- **Session polls**: `/session_poll` posts a poll with candidate dates for a game session; players join and vote with buttons. `/sessions` lists open polls and upcoming sessions, `/session_cancel` cancels one.

`/help` explains every command inside Discord, and `/ping` checks that the bot is online.

## Contents

- [Commands at a glance](#commands-at-a-glance)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [/download](#download)
- [CS2 stats](#cs2-stats)
- [Auto-posted matches](#auto-posted-matches)
- [Activity tracking](#activity-tracking)
- [Session polls](#session-polls)
- [/help](#help)
- [Data and storage](#data-and-storage)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Development](#development)
- [Changelog and license](#changelog-and-license)

## Commands at a glance

All commands are slash commands. There are no prefix commands, and the bot reads no messages except Toasty's replies to `;pokemon` when [Toasty tracking](#toasty-pokemon-catches) is turned on. Unless noted, anyone who can see a command can run it.

| Command | Parameters | Reply | Cooldown (per user) |
|---|---|---|---|
| `/ping` | none | public | none |
| `/download` | `url`, `format` (mp3 or mp4, default mp4), `start`, `end` | public (errors are private) | 30 s, only after a valid request; refunded unless a file was posted |
| `/link` | `steam` (SteamID64, profile link or custom URL name) | private | 15 s, only after valid input; not refunded |
| `/unlink` | none | private | none |
| `/scoreboard` | `player` (default you), `match` (a Leetify match id, default latest) | public | 10 s, shared with `/profile`; refunded unless an embed was posted |
| `/profile` | `player` (default you) | public | 10 s, shared with `/scoreboard`; refunded unless an embed was posted |
| `/leaderboard` | `metric` (default Leetify rating) | public | 30 s; refunded unless players were shown |
| `/activity` | `month` (`YYYY-MM`, default this month) | public (errors are private) | 10 s, shared with `/myactivity`; refunded unless someone was listed or the month has Pokémon data |
| `/myactivity` | `user` (default you), `month` (`YYYY-MM`, default this month) | public (errors are private) | 10 s, shared with `/activity`; refunded unless an embed was posted |
| `/session_poll` | `name`, `days`, `times`, `weeks` (1-8, default 2), `start` (`YYYY-MM-DD`, default tomorrow), `duration_hours` (1-12, default 4), `min_players` (2-25), `round_hours` (1-336) | public poll (errors are private); Join/Leave/Vote and refused Close round/Confirm replies are private; round results and confirmed sessions are posted publicly | 30 s; refunded unless the poll was posted |
| `/sessions` | none | public list (errors are private) | 10 s; refunded unless the list was posted |
| `/session_cancel` | `item` (pick an open poll or upcoming session from the autocomplete) | public confirmation; refusals are private | none |
| `/help` | `command` (a command name with autocomplete, default: overview of every command), `public` (default false) | private unless `public` is true (unknown names are always private) | 15 s, only for `public` replies; refunded if the reply could not be sent |

Cooldowns live in memory and reset when the bot restarts. A throttled request gets a private reply with the seconds left ("Slow down!" for every command except `/help`, which suggests leaving out `public`).

## Quick start

You need Python 3.12+ (the repo pins 3.13 in `.python-version`; uv fetches it), [uv](https://docs.astral.sh/uv/), ffmpeg and deno (see [System dependencies](#system-dependencies)). The [dev container](#dev-container) has all of them.

1. Create a dev bot and a private test server in the Discord Developer Portal and invite the bot. [Details](docs/deployment.md#1-create-a-discord-application).
2. `cp .env.example .env`, then fill in `DISCORD_TOKEN` (the dev bot) and `GUILD_ID` (the test server).
3. `uv sync`
4. `./scripts/check.sh` (ruff, format check, mypy strict, pytest).
5. `uv run python -m ninja_bot` (Ctrl+C stops it).

`uv run python scripts/smoke.py` is a quick live check: it logs in, waits for `on_ready`, lists the guild's synced commands and exits. Production setup (Debian 13 LXC, systemd, updates, rollback) is in [docs/deployment.md](docs/deployment.md).

## Configuration

The bot reads its settings from environment variables and from a `.env` file ([`.env.example`](.env.example) is the template). A variable that is already set in the process (for example systemd `Environment=`) wins over `.env`. A missing required value or a non-positive number stops the bot at startup with a `ConfigError` (a non-numeric `GUILD_ID` fails with a plain `ValueError`).

| Variable | Default | Required | Effect |
|---|---|---|---|
| `ENVIRONMENT` | `dev` | no | `dev` or `prod`. Only `dev` is special: it makes `GUILD_ID` mandatory and is the only value `scripts/smoke.py`, `scripts/capture_leetify.py` and the Ralph loop accept. Any other value counts as non-dev, and an unset value counts as `dev`, so set `ENVIRONMENT=prod` explicitly in production. |
| `DISCORD_TOKEN` | none | yes | The bot token. Dev and prod use different tokens. |
| `GUILD_ID` | none | yes in dev | When set, slash commands sync to that server only (instantly, on every start). When unset, they sync globally, which can take minutes to show up. Leave it unset in prod. Must be a number. |
| `MAX_DURATION_SECONDS` | `3600` | no | Longest video (or clip) `/download` accepts. Positive integer. |
| `LEETIFY_API_KEY` | none | no, recommended | Sent to Leetify as a bearer token. Get one at [leetify.com/app/developer](https://leetify.com/app/developer). Without it, requests are sent unauthenticated. |
| `CS2_CHANNEL_ID` | none | no | Channel for [auto-posted matches](#auto-posted-matches). Unset means no auto-posts and no polling at all. Positive integer. |
| `CS2_POLL_SECONDS` | `300` | no | How often to check Leetify when `CS2_CHANNEL_ID` is set. Positive integer. Each cycle makes one Leetify request per linked player, so don't set it very low. |
| `DATA_DIR` | `data` | no | Directory for `cs2.sqlite3` (links and the posted-match log) and `activity.sqlite3` (voice sessions, Toasty `;pokemon` events and posted monthly reports) and `sessions.sqlite3` (session polls, their slots, players and votes, confirmed sessions, sent reminders and nudges). A relative path is relative to the process's working directory. |
| `ACTIVITY_CHANNEL_ID` | none | no | Channel for the automatic monthly activity report (see [Monthly auto-post](#monthly-auto-post)). Unset means no monthly auto-post. Positive integer. |
| `ACTIVITY_TIMEZONE` | `UTC` | no | IANA time zone name (for example `Europe/Amsterdam`) that decides where activity months start and end. An unknown name stops the bot at startup with a `ConfigError`; empty means `UTC`. |
| `SESSIONS_TIMEZONE` | `ACTIVITY_TIMEZONE` | no | IANA time zone name in which session poll slot times (for example `19:00`) are read and shown. Unset or empty means the same zone as `ACTIVITY_TIMEZONE` (so `UTC` when neither is set). An unknown name stops the bot at startup with a `ConfigError`. |
| `TOASTY_BOT_ID` | none | no | User ID of the Toasty bot. When set, the bot tracks `;pokemon` catches and requests the privileged **Message Content** intent, which must then also be enabled in the Developer Portal. Unset means no Toasty tracking and no privileged intent. Positive integer. |

**Intents:** the bot uses `discord.Intents.default()`. It needs no privileged intent unless `TOASTY_BOT_ID` is set: then it also requests **Message Content** (to read Toasty's replies to `;pokemon`), and you must enable that intent on the bot's *Bot* tab in the Developer Portal too, or Discord refuses the connection at startup. It never needs Server Members or Presences; members are looked up over the REST API instead. [Activity tracking](#activity-tracking) uses the Voice States intent, which is part of the default set (not privileged).

**Invite permissions:** View Channels, Send Messages, Embed Links, Attach Files and Read Message History (permission integer `117760`), with the scopes `bot` and `applications.commands`. The auto-post channels need a few extra things; see [Auto-posted matches](#auto-posted-matches) and [Monthly auto-post](#monthly-auto-post). `/session_poll` needs View Channel, Send Messages and Embed Links in the channel it is run in, plus Read Message History there for the vote nudges, which are replies to the poll message (see [Session polls](#session-polls)). Session polls need no new intent. Full invite walkthrough: [docs/deployment.md](docs/deployment.md#1-create-a-discord-application).

**Logging:** INFO level to stderr (journald captures it under systemd). On start the bot logs `Logged in as <name> (env=<ENVIRONMENT>)`.

**Errors:** if a command crashes, the user gets a generic private "something went wrong" reply and the traceback is logged. Internal details are never shown in Discord.

## /download

`/download url:<link> [format:<mp3|mp4>] [start:<time>] [end:<time>]` fetches a video with yt-dlp and posts the file plus an info embed. `format` defaults to mp4.

| Parameter | Meaning |
|---|---|
| `url` | An http(s) link to a single video. |
| `format` | `mp3` (audio) or `mp4` (video). Default `mp4`. |
| `start` | Optional clip start (see [Clips](#clips)). Up to 32 characters. |
| `end` | Optional clip end. Needs `start`. Up to 32 characters. |

### What gets downloaded

- Links to one video only. A playlist link is refused, but a video link with `&list=` downloads just that video. Live streams are refused. A video with an unknown duration is refused.
- Videos longer than `MAX_DURATION_SECONDS` (default 3600 = 1 hour) are refused. For clips the cap applies to the clip length, so you can clip a section out of a video longer than the cap.
- **Upload limit:** the bot picks the best quality that fits the server's upload limit (`guild.filesize_limit`: 10 MiB by default, more on boosted servers). Outside a server (DMs) it assumes 10 MiB.
  - **mp3:** tries 192, 128, 96 then 64 kbps and takes the first whose estimated size fits. If even 64 kbps is too big, the audio is "too long to fit this server's upload limit".
  - **mp4:** tries 1080p, 720p, 480p, 360p then 240p, but only heights the video actually has (so no step re-downloads the same format). After each download the real file size is checked; an oversized file is deleted and the next lower quality is tried. If even the lowest is too large, you get a "too large" reply. mp4 requests are always remuxed to mp4, so webm or mkv sources still produce `.mp4`.
- Output file names come from the video title (cut to 100 bytes, restricted to safe characters).

### Clips

Add `start` (and optionally `end`) to post only part of a video, for example `/download url:<link> format:mp3 start:1:30 end:2:45`.

- **Accepted time formats:** plain seconds (`90`, `90.5`); `m:ss` or `h:mm:ss` with optional tenths (`1:30`, `1:02:03`, `1:30.5`; seconds and minutes must be under 60); units in h/m/s order (`1h2m3s`, `2m`, `45s`, `1m30.5s`, decimals only on seconds). Digits must be ASCII.
- `start` alone clips to the end of the video.
- `end` without `start` is rejected, as is an `end` at or before `start`, or a `start` at or past the end of the video.
- An `end` beyond the video is clamped to its length. A clip must be at least 1 second long.
- Cuts are made at exact times (keyframes are forced at the cuts), so mp4 clips take longer to produce.
- Clips skip yt-dlp's whole-video size filter when picking a format (that size is for the full video); the clip's real file size is checked after download.
- Invalid `start` or `end` values get a private reply before any download starts and don't use up your cooldown.

### Queueing and cooldown

- One download runs at a time for the whole bot. A request that has to wait shows "Queued behind another download, yours starts as soon as it finishes." and gives up with "The bot is busy right now, try again in a few minutes." after 5 minutes.
- Each user has a 30-second cooldown that starts after a valid request (a bad URL or clip doesn't count). It is refunded unless a file was actually posted, so a failed download can be retried right away.
- The final result is a public message in the channel. Validation and cooldown replies are private.

### The embed

The title is the video title, linked to the video. Fields appear when known, in this order: Uploader, Duration, Clip (`start → end (length)`), Views, Uploaded. The thumbnail is shown and the footer says `MP3` or `MP4`. "Too large" and "unsupported" replies include the embed too, so people can see what they asked for.

### Error replies

| Situation | Reply |
|---|---|
| Not an http(s) link | "That doesn't look like a valid http(s) link." |
| Link can't be read (private, removed, unsupported) | "I couldn't read that link. It may be private, removed or unsupported." |
| Playlist | "Playlists aren't supported; send a link to a single video." |
| Live stream | "Live streams can't be downloaded." |
| Too long | "That video is too long (max M:SS)." / "That clip is too long (max ...)." |
| Doesn't fit the upload limit | "... too long to fit this server's upload limit." / "Even the lowest quality is too large for this server's upload limit." |
| Bad clip time | "That clip time doesn't look right. Use seconds or a time like `90`, `1:30`, `1:02:03` or `1m30s`." |
| Download failed | "The download failed. The link may be private, removed or unsupported." |
| Anything else | "Something went wrong while downloading that. Please try again later." |

### System dependencies

| Dependency | Why |
|---|---|
| ffmpeg | Audio extraction, merging, remuxing to mp4 and clipping. |
| deno | The JavaScript runtime yt-dlp needs for YouTube. Without it downloads fail with `HTTP Error 403`. Pinned to **2.9.7** (verified with yt-dlp 2026.8.19) in `Dockerfile.dev` and the deployment guide. |
| yt-dlp + `yt-dlp-ejs` | Installed by `uv sync` through the `yt-dlp[default]` extra. |

Check the runtime with `uv run yt-dlp -v -F <url>` and look for `JS runtimes:`.

**yt-dlp upkeep:** sites change often. When extractors break, run `uv lock --upgrade-package yt-dlp`, then `uv sync`, `./scripts/check.sh`, and redeploy. Bump the deno tag in `Dockerfile.dev` (and the deployment guide) alongside it when yt-dlp asks for a newer deno, then re-check a YouTube download.

## CS2 stats

CS2 stats come from the Leetify public API (`api-public.cs-prod.leetify.com`). Players need a **public Leetify profile**. Everyone who wants stats links their Steam account once with `/link`.

### /link and /unlink

`/link steam:<SteamID64, link or name>` links your Discord account to a Steam account. Both replies are private.

| Accepted input | Example |
|---|---|
| SteamID64 (17 digits) | `76561198000000000` |
| Profile link | `https://steamcommunity.com/profiles/76561198000000000` |
| Custom URL link | `https://steamcommunity.com/id/mycustomname` |
| Custom URL name | `mycustomname` (2 to 32 letters, digits, `_` or `-`) |

Links may have `www.`, a trailing `/`, a `?query` or a `#fragment` (so pasted share links work). Input is limited to 200 characters.

How it works:

1. Invalid input gets an immediate private reply and uses no cooldown.
2. If you give a SteamID64 or `/profiles/` link for the account you are already linked to, the reply is "You're already linked to that Steam account." with no network call and no cooldown.
3. Otherwise the 15-second `/link` cooldown starts (it is not refunded, so repeated bad lookups can't burn the shared quota).
4. A custom URL name is resolved to a SteamID64 through Steam's public profile page (`steamcommunity.com/id/<name>/?xml=1`). No Steam API key is needed. Redirects are refused, the response is capped at 64 KiB, and the timeout is 10 seconds. Only the ID is extracted (with a regex; the body is never parsed as XML).
5. The bot checks the account exists on Leetify, then saves the link. Linking again replaces the old link.
6. The reply is `Linked to **<name>** on Leetify.` For a custom URL name it adds the resolved Steam ID so you can check it is yours.

Steam lookup problems:

| Situation | Reply |
|---|---|
| Name doesn't exist | "No Steam profile uses that custom URL. Check the spelling, or use your 17-digit SteamID64." |
| Steam rate limits the lookup | "Steam is rate limiting name lookups right now. Try again later, or use your 17-digit SteamID64 instead." |
| Steam unreachable or errors | "Steam couldn't be reached to look up that name. Try again later, or use your 17-digit SteamID64 instead." |

`/unlink` removes your link ("Your Steam account is no longer linked." or "You don't have a linked Steam account."). It has no cooldown.

Several Discord users can link the same Steam account; for reverse lookups (matching players in a match to Discord members) the lowest Discord ID wins.

### /scoreboard

`/scoreboard [player] [match]` posts the full scoreboard of your latest match, of `player`'s latest match, or of a specific Leetify match id. The reply is public.

The embed is titled `<map> - <score>:<score>` and stamped with the match's finish time. Its colour shows the focus player's result (green win, red loss, light grey tie or unknown). From top to bottom:

1. **Team tables** (the focus player's team is shown as "Your team"): monospace tables with `Player K/D/A ADR HS% Rating`, sorted by rating then kills. A star marks the player with the most MVPs (ties go to the higher rating). Names are 12 display columns wide (wide characters count double, so CJK and emoji names stay aligned, as far as Discord's fonts allow). If a table would be too long, rows are dropped with `+N more`.
2. **Other players**: anyone without a team number or on a third team.
3. **Awards**: up to 5 fun awards, see below.
4. **Friends in this match**: linked friends who are members of this server, as `@mention as SteamName`, at most 10 lines. Mentions never ping anyone.
5. **The focus player's stats**: K/D/A, ADR, HS%, rating, preaim (degrees), reaction time (ms) and multi-kills.
6. **Leetify breakdown**: aim, positioning and utility (0 to 100), clutch and opening (signed ratings).
7. **Recent form**: the newest 5 results as 🟩 win, 🟥 loss, 🟨 tie or ⬜ unknown, with the average rating.

Detailed stats, the breakdown and form are only shown for the focus player, not for every friend.

Things worth knowing:

- Every player in the match is listed. If Leetify's match history only returns your own entry, the bot re-fetches the full match by id.
- Friends appear by Discord display name. Members are looked up through the REST API (`fetch_member`, at most 10 lookups per scoreboard), so no extra intent or permission is needed. A linked friend who left the server falls back to their Steam name.
- Player names from Leetify are escaped, so markdown in a name is shown as plain text.
- The match and the profile are fetched at the same time. If only the profile fails, the scoreboard is still posted without the breakdown and form.

**Awards** (at most 5 per match and at most 2 per player). Categories are checked in this order, and one with no qualifier is skipped, so the last ones only show up when earlier ones are missing:

| Award | Goes to | Needs |
|---|---|---|
| Headhunter | highest headshot % | 5+ kills |
| Utility Wizard | most flash assists | 1+ |
| Trader | most trade kills | 2+ |
| Ace Hunter | most rounds with 3, 4 or 5 kills | 1+ |
| Lightning Reflexes | lowest reaction time | above 0 ms |
| Ghost | highest share of rounds survived | n/a |
| Whiffer | lowest accuracy | 10+ kills |
| Friendly Fire Hazard | most shots into teammates | 3+ |

Ties go to more kills, then to the name.

### /profile

`/profile [player]` posts a player's profile (public). The title is their name, and the embed has: **Ranks** (Premier rating, FACEIT level, Leetify rating, whichever are known), **Winrate**, **Matches**, **Ratings** (10-character bars: aim, positioning and utility out of 100, clutch and opening on a signed scale) and **Recent form**. Missing data is left out.

### /leaderboard

`/leaderboard [metric]` ranks the linked players by one metric: Leetify rating (default), aim, positioning, utility, clutch, opening duels or winrate. The reply is public.

- **Rating** is the average Leetify rating over the player's recent matches (it is not a profile field). The other metrics come from the profile.
- Each line shows the medal or rank, the player as a mention (it never pings), their value, a trend and a streak, for example `🥇 @Sam · +0.05 ↑ +0.03 🔥3`.
- **Trend:** the average rating of the newest 5 matches against the 5 before. `↑ +0.04` or `↓ -0.02` when the difference is at least 0.01, `→` when it is smaller. No trend if either window has fewer than 3 rated matches.
- **Streak:** a run of 3 or more wins (`🔥3`) or losses (`🧊3`) ending at the newest match. Only shown together with a trend.
- **Ranking:** best first, ties share a rank (1, 1, 3), the top 3 get 🥇🥈🥉, and players without a value for the metric come last as `-`.
- **Who is checked:** every stored link (not only people currently in the server), sorted by Discord ID, cut at the first **25**. Profiles are fetched one at a time. A player whose profile is private or unavailable is skipped; if Leetify rate limits the bot, it stops and shows what it has.
- **Footer notes**, joined with ` · `: `Skipped: N (private or unavailable)`, `Stopped early, Leetify is rate limiting us: N not loaded`, `Only the first 25 linked players are checked`.
- The ranking is split across fields if it is long (`Ranking`, `Ranking (cont.)`). At most 25 rows are shown.
- If nobody has linked, the embed says "Nobody is linked yet. Use `/link` to link your Steam account." and the cooldown is refunded.

### Caching

| What | How long | Notes |
|---|---|---|
| Leetify profile per Steam ID | 5 minutes | Errors are never cached. Running `/leaderboard` or `/profile` twice in a row doesn't hit Leetify twice. |
| Discord display name per member | 5 minutes | Looked up in the bot's cache, then over REST. |
| "Not a member of this server" | 1 minute | Only for a real 404; transient errors are never cached. |

Match lists and matches are not cached. Caches are in memory and reset on restart.

### Error replies

| Situation | Reply |
|---|---|
| You haven't linked | "You haven't linked a Steam account yet. Run `/link` first." |
| `player` hasn't linked | "**Name** hasn't linked a Steam account yet. They can run `/link` to do it." |
| Leetify doesn't know the player | "Leetify doesn't know that player. Make sure they have a Leetify account." |
| Wrong `match` id | "Leetify has no match with that id. Check the id, or leave `match` empty for the latest match." |
| Private profile | "That Leetify profile is private, so its stats can't be shown." |
| No recent matches | "No recent matches found on Leetify for that player." |
| Leetify rate limit | "Leetify is rate limiting us right now. Please try again later." |
| Leetify down or unexpected response | "Leetify couldn't be reached or sent something unexpected. Try again later." |

## Auto-posted matches

With `CS2_CHANNEL_ID` set, the bot checks Leetify every `CS2_POLL_SECONDS` (default 300) for new matches of linked players and posts a scoreboard for each one in that channel. Unset, nothing runs.

- **Silent seeding:** the first time the bot sees a linked player (after a new `/link`, or the first start with this feature), it records their current match history as already posted and announces nothing. Only matches played after that are posted.
- **One post per match:** a match with several linked friends is posted once. The embed highlights the squad: a `▸` marks them in the team tables, and a `Squad` field lists up to 10 friends (best rating first) as `@mention as SteamName · K/D/A · ADR · rating`. Mentions never ping. A linked friend who left the server is still listed with their Steam name, just without a mention. The embed colour follows the squad's result (green win, red loss, light grey for a tie or a squad split across both teams). There is no per-player breakdown or form, only the shared scoreboard and awards.
- **Complete scoreboards only:** if the full match (all players) can't be fetched, the post waits and is retried next cycle instead of posting a partial scoreboard. Retries stop once the match passes the age limit.
- **3-hour age limit:** matches that finished more than 3 hours ago are skipped (and marked as posted), so a bot that was offline for a while doesn't flood the channel. Matches without a timestamp are never dropped by this.
- **At most 3 posts per cycle**, oldest first; the rest follow in later cycles. The age limit and this cap are constants in `cs2/poller.py`, not settings.
- **Rate limits:** when Leetify rate limits the bot, it posts what it already has, skips the next cycle, and then resumes from the player where it stopped, so every friend gets checked.
- **Posted matches are remembered** in `cs2.sqlite3` (the newest 500), so a restart never reposts them. A match is marked as posted even if sending it failed, so a broken post is not retried every cycle.
- Matches can take a while to appear on Leetify after they end, and players with a private Leetify profile are never posted.

**Channel requirements.** Auto-posts are normal messages, not interaction replies, so the channel permissions matter here. The bot needs View Channel, Send Messages (Send Messages in Threads for a thread) and Embed Links in that channel. The channel can be a text channel, a thread or a voice channel's text chat. Check the channel's permission overrides, not just the server role. If something is wrong the bot logs one warning per problem and posts nothing:

`Not posting CS2 matches: the CS2_CHANNEL_ID channel is <cause>`

where the cause is `missing` (not found), `not a text channel`, `missing View Channel, Send Messages or Embed Links`, or `unavailable (Discord error)` (Discord failed to look the channel up, e.g. an outage; the next cycle tries again). The warning repeats only after the channel has worked again.

## Activity tracking

The bot records how long each member spends in voice channels. It always runs; there is nothing to turn on.

- **What is counted:** time connected to any voice or stage channel of the server (`GUILD_ID` when set, otherwise every server the bot is in). Muting, deafening, streaming or camera changes don't split or stop a session; moving to another channel ends the session and starts a new one in the new channel. Bots are never tracked.
- **AFK excluded:** the server's AFK channel counts as not being in voice. Being moved there (by Discord or by hand) ends the session; leaving it for a normal channel starts a new one.
- **Restarts:** every open session gets a heartbeat once a minute. When the bot starts, sessions left open by the previous run (a crash, restart or update) are closed at their last heartbeat, so the downtime is not counted, and everyone currently in a non-AFK voice channel gets a fresh session. Time spent in voice while the bot was offline is lost (up to a minute before the stop as well).
- **Intents:** only the Voice States intent (part of the defaults, not privileged). No Server Members or Presences intent is needed.
- Sessions are stored in `activity.sqlite3`; see [Data and storage](#data-and-storage).

### /activity

`/activity [month]` posts the server's voice overview for one month, publicly:

- **Leaderboard:** everyone with voice time that month, most time first: `🥇 @member **12h 30m** · 18 days` (total time and active days). Members with the same total share a rank. At most 25 rows; the rest are counted as `+N more` in the footer. A day counts as active with at least 5 minutes in voice (summed over that day's sessions).
- **Wall of shame:** the 3 members with the least time (🐌), including members with no voice time that month (`0m`). Only members the bot already knew by the end of that month (first voice session before it ended) who were in voice within the 90 days before its end (or any time since) can appear, so long-gone members and people who joined later are left out. Members who have left the server are skipped too (checked with Discord when the overview is built), and the next lowest member takes their place.
- **Awards:** 🦉 Night owl (most time between 00:00 and 06:00), 🏃 Marathon (longest single session), 📅 Most consistent (most active days; ties broken by total time), 📈 Biggest climber and 📉 Biggest drop (change in total time against the month before; only for completed months, and only when the month before has data), 🐦 Early bird (most time between 06:00 and 09:00). Tied winners share the award; an award nobody earned is left out.
- **Together:** the pair of members who spent the most time in the same channel at the same time (at least 10 minutes), the busiest hour and weekday, and the server's total voice time.
- **Pokémon:** only when the month has [Toasty ;pokemon](#toasty-pokemon-catches) data, see [Pokémon stats](#pokémon-stats).
- A month without any voice time shows "Nobody was in voice in <month>." instead (still with the Pokémon field when there are catches or cooldowns).

### /myactivity

`/myactivity [user] [month]` posts one member's month (default: you), publicly: total voice time and rank, active days out of the days in the month, current and longest streak of active days, number of sessions with the longest and average session, a strip with one square per day (🟩 active, ⬜ not), and any awards they won (including ⏳ Impatient). Streaks count active days in a row within the viewed month only, so they restart on the 1st. For an ended month the current streak is the run ending on its last day; for the current month it ends today, or yesterday while you haven't been in voice yet today, so a quiet morning doesn't break it. When the month has Pokémon data, a **Pokémon** field shows their catches, shinies and cooldown hits (`0 catches · 0 shinies · 0 cooldown hits` if they didn't play). A member with Pokémon data but no voice time that month gets the "wasn't in voice" reply.

### Months, cooldown and errors

- **`month`** is `YYYY-MM` (for example `2026-09`; spaces around it are ignored). Leave it empty for the current month. The earliest accepted month is `2015-01` (`MIN_YEAR`, the year Discord launched). Months, days, hours and streaks all use `ACTIVITY_TIMEZONE` (see [Configuration](#configuration)); the embed footer names it. The current month runs up to now, including sessions that are still open.
- **Cooldown:** 10 s per user, shared by both commands, taken only after the month is valid. It is refunded when the reply is an error, when `/activity` finds an empty month (no voice time and no Pokémon data), or when the reply could not be sent.
- **Errors** are private replies: a month that isn't `YYYY-MM` or is before `2015-01` ("Use a month like `2026-09` (YYYY-MM).") or hasn't started yet ("That month hasn't started yet."), a member without voice time that month ("You weren't in voice in <month>." / "**Name** wasn't in voice in <month>."), and "Something went wrong while loading voice activity. Please try again later." for anything unexpected.
- Members appear as mentions, but nobody is pinged.

### Monthly auto-post

With `ACTIVITY_CHANNEL_ID` set, the bot posts the `/activity` overview of the previous month in that channel automatically, once per month. Without it there is no auto-post (the commands still work).

- **When:** the bot checks once an hour (and right after it starts) whether a month has ended at midnight in `ACTIVITY_TIMEZONE`. The post for September arrives within an hour after 1 October 00:00 local time.
- **One post per month:** a posted month is recorded in `reports_posted` (see [Data and storage](#data-and-storage)) and never posted again. A month is also recorded when the send fails (for example Discord refuses it), so a broken post is not retried every hour. If the overview itself can't be built (an unexpected error, logged as `Could not build the activity overview for <YYYY-MM>; retrying`), the month is not recorded and the next hourly check tries again.
- **Catching up:** if the bot was offline when a month ended, it posts the missed months oldest first, one per hourly check. Months before the first recorded voice session and months without any voice time are skipped.
- **Channel:** the bot needs **View Channel, Send Messages and Embed Links** there (Send Messages in Threads for a thread); a text channel, a thread or a voice channel's text chat. If the channel can't be used, nothing is sent and the month stays unposted, so it is posted once the channel works. The bot logs one warning per cause:

  `Not posting the monthly activity overview: the ACTIVITY_CHANNEL_ID channel is <cause>`

  where the cause is `missing`, `not a text channel`, `missing View Channel, Send Messages or Embed Links`, or `unavailable (Discord error)` (a Discord error while looking the channel up; the next hourly check tries again).
- Members appear as mentions, but nobody is pinged.

### Toasty ;pokemon catches

With `TOASTY_BOT_ID` set to the user ID of the Toasty bot, the bot also records what members catch with Toasty's `;pokemon` command. Without it nothing is read or recorded.

- **What is tracked:** a member's message that is exactly `;pokemon` (any case, one optional space after the `;`, nothing else; `;pokemon list` doesn't count), then Toasty's reply to that message within 5 minutes. A reply `you've caught a <Name>!` is recorded as a catch of that Pokémon (`Shiny <Name>` as a shiny catch); `You need to wait ... before catching another Pokemon!` is recorded as a cooldown. The reply's text and embeds are both read, and an edited reply is read again (a reply is never counted twice). The catch goes to the author of the `;pokemon` message, not to whoever the reply mentions.
- **What is stored:** only the parsed fields (who, when, catch or cooldown, the Pokémon name and whether it was shiny) in `pokemon_events`; never the message text. See [Data and storage](#data-and-storage). Toasty events alone never put a member on the wall of shame; only voice sessions do.
- **Ignored:** `;pokemon` sent by bots, other Toasty messages, replies by other bots, replies to messages the bot didn't see (or saw more than 5 minutes earlier), and other servers when `GUILD_ID` is set.
- **Message Content intent:** reading these messages needs the privileged **Message Content** intent. The bot requests it only when `TOASTY_BOT_ID` is set, and it must also be enabled in the Developer Portal (see [Configuration](#configuration)).

#### Pokémon stats

When a month has Toasty events (catches or cooldowns), `/activity`, the monthly auto-post and `/myactivity` include a **Pokémon** field. In `/activity` and the auto-post it shows, in this order:

- **Top catchers:** members with at least one catch, most catches first: `🥇 @member **12 catches**`. Members with the same count share a rank. At most 5 rows, then `+N more`.
- **Shinies:** `✨ @member: shiny <Name>` per shiny catch, oldest first; at most 5, then `✨ +N more`.
- **Most caught:** `🏆 Most caught: <Name> (3)`, or every tied Pokémon in name order (`Eevee, Pikachu (2 each)`; at most 5 names, then `+N more`).
- **⏳ Impatient:** the member(s) whose `;pokemon` hit Toasty's cooldown most often that month, only with at least 3 hits: `⏳ Impatient: @member (5 cooldown hits)`. It is listed here, not in the Awards field; on `/myactivity` it appears under the member's awards.
- **Server totals:** `🎯 Server: 12 catches · 4 cooldown hits`.

Pokémon names are shown as plain text (markdown escaped) and cut at 32 characters. A month counts by the reply's time in `ACTIVITY_TIMEZONE`. The monthly auto-post only happens for months with voice time, so a month with only Pokémon data is not auto-posted (it still shows in `/activity`).

## Session polls

A GM (whoever runs the command) starts a poll to find a date for a game session. The bot offers candidate slots, players join and vote with buttons, and the poll message keeps itself up to date. When a round closes, the GM confirms one of the fitting dates, or the poll moves on to the next weeks.

### /session_poll

`/session_poll name days times [weeks] [start] [duration_hours] [min_players] [round_hours]` posts the poll in the current channel, publicly. It only works in a server.

| Parameter | Accepted | Default |
|---|---|---|
| `name` | 1-60 characters (spaces around it are ignored) | required |
| `days` | comma-separated weekdays: `fri`, `friday` (any case), `weekend` (Sat + Sun), `weekdays` (Mon-Fri); for example `fri,sat`; at most 64 characters | required |
| `times` | comma-separated 24-hour start times `H:MM` or `HH:MM`, for example `19:00,14:30`; at most 64 characters | required |
| `weeks` | 1-8: how many weeks from `start` to offer | 2 |
| `start` | first day to offer, `YYYY-MM-DD`, today or later | tomorrow |
| `duration_hours` | 1-12: how long a session lasts | 4 |
| `min_players` | 2-25: players needed for a date to fit | every joined player |
| `round_hours` | 1-336: how long each voting round stays open | no deadline |

- **Slots:** every chosen weekday at every chosen time from `start` for `weeks` weeks (start date included, the day `weeks` weeks later excluded), in `SESSIONS_TIMEZONE` (falls back to `ACTIVITY_TIMEZONE`, see [Configuration](#configuration)). Slots that already started are dropped. A time that doesn't exist on a daylight-saving change day is skipped; a time that happens twice uses the first one. A poll offers at most 25 slots (Discord select menus hold 25 options).
- **The poll message:** the name, `Round 1 of 6`, the GM, the session length and `min_players` (when set), one line per slot with its time (shown in everyone's own time zone) and ✅ / 🤔 counts, the round deadline (when `round_hours` is set), `Joined` (every player as a mention) and `Waiting for` (players without a vote this round, `+N more` when it doesn't fit). Mentions never ping.
- **Join / Leave buttons:** the GM is joined automatically. **Join** adds you (at most 25 players), **Leave** removes you and your votes; both update the poll message and confirm privately. The GM can't leave their own poll. Pressing Join twice, Leave without having joined, or either button on a poll that no longer takes votes gives a private reply and changes nothing. Buttons keep working after the bot restarts.
- **Vote button:** opens a private form (it works for 10 minutes) with two multi-selects over the current round's slots, **✅ Works for me** and **🤔 Only if needed**, already filled in with your saved votes. The options read like `Fri 6 Nov 19:00` in `SESSIONS_TIMEZONE` (select menus can't show times in your own zone; the prompt names the zone). **Save** replaces all your votes for the round: a slot picked in both lists counts as works, a slot in neither as a no. **Save** with nothing picked in either list is refused privately with "Pick at least one slot, or press **None of these work** if none of them suit you." and the form stays open, so an early Save never counts as a no for everything. **None of these work** saves a no for every slot. Either way the form closes with "Your votes are saved." and the poll message updates its counts and `Waiting for`. If the poll message can't be edited (deleted, missing permissions), your vote still counts. You must press **Join** first ("You haven't joined this poll. Press **Join** first."); on a poll that no longer takes votes you get "This poll isn't taking votes any more.", also when you press Save after the poll closed.
- **Close round button:** only the GM or someone with **Manage Events** can press it; anyone else gets "Only the GM or someone with **Manage Events** can do that." privately. A round also closes on its own as soon as every joined player has saved a vote (None of these counts), but only once at least two players (the GM and one more) have joined, so the GM's own vote never closes the round alone; or it closes when its `round_hours` deadline passes (see Deadlines). Pressing Close round on a round that is already closed says "This round is already closed." privately and changes nothing.
- **When a round closes:** the old poll message keeps its last counts, loses its buttons and says "**Round N is closed.**". Then one of three things is posted in the channel:
  - **A date fits:** a slot fits when the players who picked it (✅ plus 🤔) reach `min_players` (or every joined player when `min_players` isn't set) **and** the GM picked it too: the GM must attend. Up to 3 fitting slots are shown, most ✅ first, then fewest 🤔, then earliest, each with who can't make it. The message pings only the GM ("pick the date with a **Confirm** button") and has one **Confirm N: Fri 6 Nov 19:00** button per slot.
  - **Nothing fits, rounds left:** a new round opens with the same days and times over the next `weeks` weeks (a poll has at most 6 rounds). A new poll message with Join/Leave/Vote/Close round is posted, together with a summary of the new window and who still hadn't voted, and only those players are pinged. Votes don't carry over; everyone votes again on the new slots.
  - **Nothing fits in round 6** (or the next window has no usable slots): a "No date fit" message with advice (fewer days, more weeks or a lower `min_players`); the poll ends. Nobody is pinged.
- **Confirm button:** only the GM or someone with **Manage Events**; others get the same private refusal. It turns the result message into "✅ **Confirmed:** <date>." (buttons removed) and posts the session: date and time (absolute and relative), GM, length and the players, who are pinged. The players are everyone who picked that slot (✅ or 🤔), in join order. A second Confirm (a double click or another button) gets "There's no open poll with that id; it may be finished or cancelled." privately.
- **Deadlines:** with `round_hours`, a background check (every 60 s) closes the round once its deadline passes, with the same outcome as Close round. Without `round_hours` a round stays open until everyone voted (at least two players) or the GM closes it. A round left open by a restart (everyone voted, but the bot stopped before closing it) is closed by the same check.
- **Reminders:** a confirmed session gets two reminders in the poll's channel, 24 hours and 1 hour before the start: "⏰ <players> **<name>** starts in <time>!" with the session embed, pinging the players. Each is sent once, also across restarts. Reminders whose time has already passed when the session is confirmed are skipped (the announcement covers them): confirmed 5 hours ahead, only the 1-hour reminder follows; confirmed 30 minutes ahead, none. If the bot was down past the 24-hour mark it sends only the 1-hour reminder (one message, not two); nothing is sent once the session started, and cancelled sessions are never reminded.
- **Nudges:** while a round is open, players who haven't voted get a reply to the poll message ("👋 <players> please vote in **<name>** (round N).", pinging only them) once the round has been open 24 hours, then at most once per 24 hours, at most 3 times per round. A new round starts the count again; cancelled or closed polls are never nudged.
- **Channel gone:** before a deadline close, reminder or nudge the bot checks the poll's channel. If it was deleted or the bot lost View Channel, Send Messages or Embed Links there, nothing is posted and one warning per cause is logged (not one per minute); a due deadline close or reminder waits until the channel works again (a reminder only until the session starts). A send that Discord refuses anyway is logged and not retried.
- **Old messages:** if the old poll message can't be edited or a result can't be posted (deleted channel or message, missing permissions), the error is logged and the poll still moves on. Close round and Confirm keep working after a restart; buttons on a poll that's gone say "This button doesn't work any more." privately.
- **Limits:** at most 5 open polls per server.
- **Cooldown:** 30 s per user, refunded unless the poll was posted (input errors, a channel problem and a failed send cost nothing). Like the other cooldowns it resets when the bot restarts.
- **Channel:** the bot needs View Channel, Send Messages and Embed Links in the channel (Send Messages in Threads for a thread); otherwise you get "I can't post a poll here: I need View Channel, Send Messages and Embed Links in this channel." It also needs Read Message History there for the nudges (Discord only allows replies with it); this isn't checked up front, so without it each nudge is refused by Discord, logged and still counted.
- **Errors** are private replies: a blank or too long name, days not in the accepted forms ("Use weekdays like `fri,sat` or `friday, saturday` (or `weekend` / `weekdays`)."), bad times ("Use 24-hour times like `19:00,14:30`."), a bad or past start date, an out-of-range number, no slots in the window ("There are no slots in that window; pick a later start date or more weeks."), more than 25 slots ("That gives N slots (max 25); use fewer weeks, days or times."), too many open polls, and "Something went wrong with the session poll. Please try again later." for anything unexpected (logged). If the poll message can't be sent, the poll is cancelled so it doesn't count towards the limit.
- Polls, players and votes are stored in `sessions.sqlite3`; see [Data and storage](#data-and-storage).

### /sessions

`/sessions` (no parameters, server only) posts this server's open polls and upcoming sessions publicly:

- **Open polls** (voting or waiting for Confirm, oldest first): the name linked to the current poll message (bold without a link when the message id is unknown), `Round N of 6`, and either `waiting for <players>`, `everyone voted` or `waiting for the GM to confirm`.
- **Upcoming sessions** (confirmed, not cancelled, not started yet, soonest first): name, start time (absolute and relative, in everyone's own time zone) and GM; at most 10, then `+N more`.
- With neither: "No open polls or upcoming sessions. Start one with `/session_poll`."
- Mentions in the list never ping. Cooldown: 10 s per user, refunded unless the list was posted. Anything unexpected gives "Something went wrong with the session poll. Please try again later." privately (logged).

### /session_cancel

`/session_cancel item` cancels an open poll or an upcoming session of this server. Only the GM or someone with **Manage Events** can cancel; anyone else gets "Only the GM or someone with **Manage Events** can do that." privately.

- **`item`** autocompletes this server's open polls (`Poll: <name> (from Tue 3 Nov)`, the first day of the current round's window) and upcoming sessions (`Session: <name> (Fri 6 Nov 19:00)`), with dates in `SESSIONS_TIMEZONE`. Typing filters the list (case-insensitive, anywhere in the label); at most 25 suggestions. Pick a suggestion: text typed by hand that isn't one gets "Pick an open poll or upcoming session from the list; it may be finished or cancelled." privately.
- **A poll:** the poll stops (no more votes, deadline closes or nudges; it no longer counts towards the 5 open polls). Its current poll message gets "🚫 **Cancelled.** This poll no longer takes votes." and loses its buttons. A result message that was still waiting for Confirm keeps its Confirm buttons, but they refuse privately.
- **A session:** it disappears from `/sessions` and gets no more reminders; the poll message it came from gets "🚫 **Cancelled.** This session won't take place.". Sessions that already started can't be cancelled.
- The bot then replies publicly, for example "🚫 The session poll **<name>** was cancelled by <you>." (mentions don't ping). If the poll message can't be edited (deleted, missing permissions) the error is logged and the cancel still counts.
- An item that is finished, already cancelled or from another server gets "There's no open poll with that id; it may be finished or cancelled." or "There's no upcoming session with that id; it may have started or been cancelled." privately.

## /help

`/help [command] [public]` explains the bot inside Discord. The reply is private unless `public` is true.

- **Overview** (no `command`): a "Getting started" hint, then the commands grouped as Media, CS2 stats, Voice activity, Session polls and Utility, one line each with the usage and a one-line summary (for example `` `/download url:<url> [format:mp3|mp4] [start:<start>] [end:<end>]` ``). A registered command without help text is listed under "Other" with its Discord description. The footer points to `/help command:<name>`.
- **Detail** (`command` set): the command's summary and explanation, then Usage, Parameters (required or optional, choices, accepted formats), Examples and Notes.
- **Command names:** `command` has autocomplete (names that start with what you typed first, then names that contain it, up to 25). Case and a leading `/` are ignored, so `/Link` works like `link`.
- **Unknown names** get a private reply, "There is no command with that name.", with up to 3 similar names ("Did you mean: /download?") or a pointer to `/help`.
- **`public`:** `public:true` posts the help in the channel for everyone. Public replies have a 15 s cooldown per user, taken only when the help is actually posted (a failed send refunds it). A throttled request gets a private reply with the seconds left that suggests leaving out `public`. Private replies and unknown names never use the cooldown.
- Mentions in the reply never ping.
- **Where the text lives:** every help string is in `src/ninja_bot/help/content.py`. Usage lines, parameters and choices are read from the registered commands, so they can't go stale; `content.py` only adds explanations, examples and notes. Rewording or restyling `/help` means editing that one file.

## Data and storage

The bot keeps three SQLite databases under `DATA_DIR`. Each is created on first use (the directory too).

The CS2 database at `<DATA_DIR>/cs2.sqlite3` holds three tables:

| Table | Columns | Purpose |
|---|---|---|
| `links` | `discord_id` (primary key), `steam64_id` | Who linked which Steam account. |
| `posted_matches` | `match_id` (primary key), `posted_at` | Matches already auto-posted; pruned to the newest 500. |
| `seeded_players` | `steam64_id` (primary key) | Players whose old matches were recorded silently. |

The activity database at `<DATA_DIR>/activity.sqlite3` holds three tables:

| Table | Columns | Purpose |
|---|---|---|
| `voice_sessions` | `id` (primary key), `user_id`, `channel_id`, `start_ts`, `end_ts`, `last_seen` | One row per stretch of voice time (Unix timestamps). `end_ts` is empty while the member is still connected; `last_seen` is the last heartbeat, used to close sessions left open by a crash or restart. Never pruned. |
| `reports_posted` | `month_key` (primary key, `YYYY-MM`), `posted_at` | Months whose activity overview was already auto-posted. |
| `pokemon_events` | `message_id` (primary key, Toasty's reply), `user_id`, `ts`, `kind` (`catch` or `cooldown`), `pokemon` (empty for cooldowns), `shiny` | One row per Toasty reply to a member's `;pokemon` (only with `TOASTY_BOT_ID`; see [Toasty ;pokemon catches](#toasty-pokemon-catches)). No message text. Never pruned. |

The session poll database at `<DATA_DIR>/sessions.sqlite3` holds these tables:

| Table | Columns | Purpose |
|---|---|---|
| `polls` | `id` (primary key), `guild_id`, `channel_id`, `message_id`, `name`, `gm_id`, `days` (weekdays, Mon=0, comma-separated), `times` (`HH:MM`, comma-separated), `weeks`, `duration_hours`, `min_players`, `round_hours`, `round_number`, `window_start` (`YYYY-MM-DD`), `status` (`voting`, `awaiting_confirm`, `confirmed`, `failed` or `cancelled`), `created_at`, `round_started_at` | One row per session poll. `message_id` is empty until the poll message is sent. Never pruned. |
| `poll_slots` | `id` (primary key), `poll_id`, `round_number`, `start`, `end` | The candidate slots of each round (Unix timestamps). Earlier rounds' slots are kept. |
| `poll_players` | `poll_id`, `user_id` (primary key together) | Who joined each poll (the GM joins automatically). |
| `poll_votes` | `poll_id`, `user_id`, `slot_id` (primary key together), `vote` (`yes`, `if_needed` or `no`) | Each player's vote per slot. Leaving a poll deletes that player's votes. Votes of earlier rounds are kept, but only the current round's slots count. |
| `sessions` | `id` (primary key), `poll_id`, `guild_id`, `channel_id`, `message_id` (the poll message), `name`, `gm_id`, `start`, `end`, `roster` (user ids, comma-separated), `cancelled` | One row per confirmed session. Cancelling keeps the row and sets `cancelled`. Never pruned. |
| `session_reminders` | `session_id`, `offset_seconds` (primary key together) | Which reminders (seconds before the start) were already handled, so none is sent twice after a restart. |
| `poll_nudges` | `poll_id`, `round_number`, `sent_at` | One row per nudge sent, counted per round. |

In prod this directory must be writable by the service user, persistent, and **backed up** (for example with Proxmox container backups). Losing `cs2.sqlite3` means everyone has to `/link` again (they are then seeded silently, so nothing old is posted). Restoring an *old* copy is the risky case: recent matches may be posted a second time (those from the last 3 hours; older ones never are). Losing `activity.sqlite3` loses all recorded voice history (Discord has no history to rebuild it from); restoring an old copy loses the time since that backup, and the auto-post may post a month's overview again if its `reports_posted` row is missing. Losing `sessions.sqlite3` loses every open session poll (its buttons stop working), its votes and every confirmed session with its reminders; restoring an old copy can repeat reminders and nudges sent since that backup. The `data/` directory is gitignored and survives `git pull`. `/download` stores nothing and removes its temp files after each request.

## Troubleshooting

The full table (setup, systemd, Discord portal errors) is in [docs/deployment.md](docs/deployment.md#6-troubleshooting). The most common ones:

| Symptom | Likely cause |
|---|---|
| `ConfigError: DISCORD_TOKEN is not set` | `.env` missing or empty, or the bot isn't started from the repo directory. |
| `ConfigError: GUILD_ID is required when ENVIRONMENT=dev` | Set `GUILD_ID`, or set `ENVIRONMENT=prod` in prod. |
| Bot online but no slash commands | The invite lacked the `applications.commands` scope, or (prod) global sync hasn't propagated yet. |
| Commands appear twice | Dev and prod bots are in the same server, or one bot ran once with `GUILD_ID` and once without. |
| `/download` fails with `HTTP Error 403` on YouTube | deno is missing or not on `PATH`, or yt-dlp is outdated. |
| "Too large" for a video that should fit | The upload limit is the server's (10 MiB by default). Clip it, or use a boosted server. |
| `/link` by name says "Steam couldn't be reached" | Steam is down or rate limiting. Link with the 17-digit SteamID64 instead. |
| Auto-posts: nothing is posted | Check `CS2_CHANNEL_ID`, the channel permissions (see the log warning), that the player's Leetify profile is public, and that the match is under 3 hours old. A new link's old matches are deliberately not posted. |
| `PrivilegedIntentsRequired` at startup | `TOASTY_BOT_ID` is set but **Message Content Intent** is off in the Developer Portal. Enable it, or unset `TOASTY_BOT_ID`. |
| No monthly activity post | Check `ACTIVITY_CHANNEL_ID` and the channel permissions (see the log warning). The post comes within an hour after the month ends in `ACTIVITY_TIMEZONE`, and only for months with voice time. |

## Known limitations

- **Unverified Leetify data shapes:** the Leetify fixtures in `tests/fixtures/leetify/` are hand-written from the public API spec, not captured from the real API, so field names and units are unconfirmed until someone runs `scripts/capture_leetify.py`. The `/profile` bar scale for clutch and opening is a guess for the same reason.
- **Leaderboard membership:** `/leaderboard` ranks every stored link, so someone who left the server still shows up (as a mention).
- **Serial fetching:** the poller and `/leaderboard` fetch players one at a time. That is fine for a small group; it is worth revisiting if more than about 8 people link.
- **No retry cap:** a match whose full scoreboard keeps failing is retried every cycle until it passes the 3-hour age limit. It is only visible in the INFO logs.
- **Fixed limits:** the 3-hour age limit, the 3-per-cycle cap and the 25-player leaderboard cap are constants, not settings.
- **Renamed custom URLs:** Steam redirects are refused, so a custom URL that was renamed may not resolve. Use the SteamID64.
- **Steam endpoint:** the profile XML page used for custom URL names is unofficial and could change.
- **One download at a time**, bot-wide, with no persistent queue.
- **Voice only:** activity counts time connected to voice channels. Text messages, reactions, streaming and camera time are not counted (Toasty `;pokemon` replies are the only messages read).
- **No history before the feature:** Discord keeps no voice history, so tracking starts when the bot first runs with this feature; earlier months are empty.
- **Month boundaries:** months, days and hours use `ACTIVITY_TIMEZONE` (default `UTC`). Changing it later shifts which sessions fall into which day and month, and a month already auto-posted is not posted again.
- **Voice time while offline:** activity tracking only sees voice time while the bot runs. Downtime (plus up to a minute before a crash, since the last heartbeat) is not counted, and a leave missed during a gateway reconnect keeps the session open until the next leave or restart.
- **Toasty catches:** only recorded while the bot is online, and only when Toasty replies to a `;pokemon` message (catches by any other route, or replies to a message the bot missed, are not seen). The reply formats are derived from a few observed replies, so a Toasty wording change can stop the recording without an error.
- **Session polls live in their channel:** a poll is a message in the channel it was started in. Every update, result, reminder and nudge goes to that channel; there is no way to move a poll, and a deleted poll channel leaves the poll open (cancel it with `/session_cancel`).
- **Departed members in polls:** players who left the server stay joined, count towards `min_players` and the fit rule, and keep being mentioned in `Waiting for`, nudges, rosters and reminders. The GM or someone with Manage Events can cancel the poll and start a new one.
- **One session time zone:** slot times are read in one server-wide `SESSIONS_TIMEZONE` (default `ACTIVITY_TIMEZONE`); a player can't enter times in their own zone. Changing it later moves the times of new polls and later rounds; slots already offered keep their times.
- **Select labels use the server zone:** the vote form's options (`Fri 6 Nov 19:00`) are in `SESSIONS_TIMEZONE`, because select menus can't show Discord timestamps, while the poll, result and session embeds use timestamps that Discord shows in each reader's own zone. For players in another zone the two can differ.
- **No conflict detection:** polls are independent; the bot doesn't notice overlapping polls, sessions at the same time, or a player confirmed into two sessions at once.
- **Reminders and nudges while offline:** they are only sent while the bot runs (checked every 60 s). After downtime a due reminder is sent late (only the latest one), and nothing is sent once the session started. A reminder or nudge is marked as sent right after the attempt, so a refused send is not retried.
- **Monthly post after a crash:** a month is recorded as posted right after the send. If the bot stops between the two, that month's overview is posted again after the restart.

## Development

### Layout

```
src/ninja_bot/
  bot.py          NinjaBot: loads the extensions, syncs slash commands (to GUILD_ID if set, else globally)
  config.py       env-based config (load_config takes an optional dict for tests)
  posting.py      shared auto-post helpers: find the post channel (one warning per cause) and send safely
  cogs/           one thin cog per feature: core.py (/ping), media.py (/download), cs2.py (CS2 commands + auto-post loop),
                  help.py (/help), activity.py (voice tracking listener + heartbeat, /activity, /myactivity, monthly auto-post loop, Toasty listener),
                  sessions.py (/session_poll, /sessions, /session_cancel, poll buttons)
  help/           /help logic: content (all help text), commands, embed, lookup
  media/          download logic: models, planning, clip, embed, cooldown, downloader (the only module importing yt_dlp)
  cs2/            CS2 logic: models, steam, client (the only Leetify module), resolver (the only Steam module),
                  cache, store (SQLite), formatting, awards, identities, squad, embed, channels, poller
  activity/       activity logic: periods (months), reporter (which month to auto-post), formatting, stats (monthly totals/streaks/ranks), awards (monthly awards, social stats), report (monthly report/profile data), embed (overview/profile embeds), store (SQLite voice sessions + pokemon events), tracker (voice transitions), pokemon (Toasty ;pokemon parsing), pokemon_stats (monthly catches, Impatient award)
  sessions/       session poll logic: slots (weekday/time/date parsing, candidate slots), scoring (vote counts, round evaluation), store (SQLite polls, slots, players, votes, sessions), service (poll rules: create, join/leave, vote, close/confirm/cancel, /sessions overview), cancel (/session_cancel choices and item values), reminders (which reminders and nudges are due), content (user-facing strings), embed (poll, result, session and list embeds), views (poll buttons; the only discord.ui module)
scripts/          check.sh, smoke.py, capture_leetify.py, ralph/
tests/            pytest (asyncio_mode=auto), fixtures in tests/fixtures/leetify/, tests/fixtures/steam/ and tests/fixtures/toasty/
docs/             deployment.md, manual-checks.md
tasks/            the PRD of every feature, written before it was built
```

The architecture rules, module-by-module notes and conventions for contributors and coding agents are in [CLAUDE.md](CLAUDE.md). The short version: keep logic out of Discord handlers (cogs are thin wrappers around pure functions and small service classes, because bots can't invoke slash commands in tests), use `app_commands`, annotate everything (mypy strict), keep persistent data under `data/`, and request only the intents a feature needs.

### Adding a command

A new slash command needs a `CommandHelp` entry in `src/ninja_bot/help/content.py` (in the right group, with `parameter_notes` only for real parameters) and a row in the [Commands at a glance](#commands-at-a-glance) table, plus its own README section. `tests/test_help_drift.py` loads the real extensions and fails when a registered command has no help entry or README row (or the other way round), when a command or parameter has an empty Discord description, or when a help embed exceeds Discord's limits. Removing or renaming a command means updating both places too.

### Quality gate

`./scripts/check.sh` runs `ruff check`, `ruff format --check`, `mypy` (strict, on `src`) and `pytest -q`. It must pass before every commit. Tests use fakes and stubs (injectable clocks, fetchers and stores), never the network or a real Discord connection.

### Scripts

| Script | Purpose |
|---|---|
| `scripts/check.sh` | The quality gate above. |
| `scripts/smoke.py` | Live check against the dev bot: login, sync, list the guild's commands. Dev only; needs a dev token. |
| `scripts/capture_leetify.py <steam64_id>` | Dev only, needs `LEETIFY_API_KEY`. Saves the real profile, matches list and latest match, with Steam IDs and names replaced by placeholders, to `data/leetify-capture/` (gitignored), so you can compare field names and units with the hand-written fixtures. |
| `scripts/ralph/ralph.sh [max_iterations]` | The Ralph loop, see below. |

### Dev container

Needs Docker or Podman (`sudo dnf install podman podman-compose`, or Docker CE). The image (`Dockerfile.dev`, Debian 13, Python 3.13) has uv, ffmpeg, deno 2.9.7, git, jq and the `claude` CLI. The venv lives outside the repo, so the host `.venv` is never shared.

```
docker compose run --rm dev       # podman: PODMAN_USERNS=keep-id podman compose run --rm dev
claude                            # first time only: log in (persisted in a volume)
uv run python scripts/smoke.py    # inside the container
scripts/ralph/ralph.sh 10
```

The repo is bind-mounted, so commits land in your working tree, and the container sees only the dev `.env`. Rebuild the image if `Dockerfile.dev` changed.

### Feature workflow (Ralph)

Features are built with the Ralph loop, one small story per iteration:

1. Plan the feature and settle design decisions.
2. Write a PRD (`ralph-skills:prd`) to `tasks/prd-<feature>.md`. Stories are small, ordered by dependency, and verifiable by `./scripts/check.sh`.
3. Convert it (`ralph-skills:ralph`) to `scripts/ralph/prd.json` on a `ralph/<feature>` branch.
4. Run `scripts/ralph/ralph.sh <stories + 2-3>` in the dev container. It refuses to run unless `.env` has `ENVIRONMENT=dev`, and commits as "ralph", one commit per story.
5. Review (`/code-review`) and do the manual checks in [docs/manual-checks.md](docs/manual-checks.md) against the dev bot in the test server.
6. Open a PR from `ralph/<feature>` to `main`. Per-story commits are kept (no squash). Review follow-ups get their own PRD and branch (for example `ralph/download-hardening`).

`main` is only changed through PRs (docs and workflow tweaks may go straight to `main`). Commits use Conventional Commits (`type: subject`). The full workflow is in [CLAUDE.md](CLAUDE.md#feature-workflow).

### Safety

Dev and prod use different bot tokens. Agents and Ralph only ever get the dev one. Never commit `.env` or a token, and never run anything against prod from a dev machine. Production setup and the security checklist are in [docs/deployment.md](docs/deployment.md).

## Changelog and license

What shipped in each pull request is in [CHANGELOG.md](CHANGELOG.md). There is no license file in this repository yet, so no license is granted.
