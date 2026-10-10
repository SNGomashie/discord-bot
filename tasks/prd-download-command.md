# PRD: `/download` — fetch a video as mp3/mp4 and post it with an info embed

## Introduction

Add a `/download` slash command so a member of the private server can paste a video link, optionally pick `mp3` or `mp4`, and have the bot post the resulting file in the channel together with an embed of key video information (title, uploader, duration, views, upload date, thumbnail). Downloading is done with **yt-dlp** (the maintained fork of youtube-dl; same Python API) behind a small wrapper, and **ffmpeg** is used for audio extraction and stream merging.

Discord limits upload size per server (base 10 MiB, higher with boosts), so the bot must pick a quality that fits the server's limit and refuse cleanly when nothing fits.

## Goals

- One slash command: `/download url:<link> [format:<mp3|mp4>]`, `format` defaulting to `mp4`.
- Post the media file plus an info embed in the channel where the command was used.
- Automatically choose the best quality that fits within the server's upload limit; otherwise refuse with a clear message.
- Keep the heavy logic pure and unit-tested with no network access; the Discord cog stays thin.
- Be safe to run on a small homelab LXC: one download at a time, per-user cooldown, duration cap, temp files always cleaned up.

## User Stories

### US-001: Dependencies, config and infrastructure
**Description:** As a developer, I need the downloader dependency, ffmpeg and a duration setting in place so later stories can build on them.

**Acceptance Criteria:**
- [ ] `yt-dlp` added as a runtime dependency via `uv add yt-dlp` (`pyproject.toml` and `uv.lock` updated)
- [ ] `[[tool.mypy.overrides]]` for `yt_dlp.*` with `ignore_missing_imports = true` so mypy strict still passes
- [ ] `ffmpeg` added to the apt packages in `Dockerfile.dev`
- [ ] `Config` in `src/ninja_bot/config.py` gains `max_duration_seconds: int` read from `MAX_DURATION_SECONDS`, default `3600`, must be a positive integer (invalid values raise `ConfigError`)
- [ ] `.env.example` documents `MAX_DURATION_SECONDS`
- [ ] Tests in `tests/test_config.py` cover the default, a custom value, and an invalid value
- [ ] `./scripts/check.sh` passes

### US-002: Media models and URL validation
**Description:** As a developer, I need typed models and URL validation so the rest of the feature works with clean data.

**Acceptance Criteria:**
- [ ] New package `src/ninja_bot/media/` (with `__init__.py`) containing `models.py`
- [ ] `MediaKind = Literal["mp3", "mp4"]`
- [ ] Frozen dataclass `VideoInfo` with: `title`, `uploader` (may be None), `duration_s` (int | None), `thumbnail` (str | None), `webpage_url`, `view_count` (int | None), `upload_date` (`datetime.date | None`), `is_live` (bool), `is_playlist` (bool)
- [ ] `parse_info(raw: dict[str, Any]) -> VideoInfo` maps a yt-dlp info dict (keys `title`, `uploader`, `duration`, `thumbnail`, `webpage_url`, `view_count`, `upload_date` as `YYYYMMDD`, `is_live`, `_type == "playlist"`), tolerating missing keys
- [ ] `format_duration(seconds: int | None) -> str` returns `m:ss` or `h:mm:ss` (e.g. `65` -> `1:05`, `3725` -> `1:02:05`, `None` -> `Unknown`)
- [ ] Exception classes in `models.py`: `MediaError` (base), `InvalidUrlError`, `UnsupportedMediaError`, `TooLargeError`, `DownloadError`; each carries a short user-facing message
- [ ] `validate_url(text: str) -> str` in `src/ninja_bot/media/planning.py` strips whitespace, accepts only `http`/`https` URLs with a host, otherwise raises `InvalidUrlError`
- [ ] Unit tests for `parse_info` (full, minimal, playlist, live), `format_duration`, and `validate_url` (valid, `ftp://`, no scheme, empty, whitespace-padded)
- [ ] `./scripts/check.sh` passes

### US-003: Download planning that fits the upload limit
**Description:** As a user, I want the bot to automatically pick a quality that fits Discord's upload limit so my download isn't rejected for size.

**Acceptance Criteria:**
- [ ] Pure function `plan_download(info: VideoInfo, kind: MediaKind, limit_bytes: int, max_duration_s: int) -> DownloadPlan` in `src/ninja_bot/media/planning.py`
- [ ] `DownloadPlan` is a frozen dataclass exposing at least: `kind`, `audio_bitrate_kbps` (mp3) or an ordered `height_ladder` of height caps (mp4) and the `limit_bytes`
- [ ] Rejects with `UnsupportedMediaError` when `info.is_live`, when `info.is_playlist`, when `duration_s` is None, or when `duration_s > max_duration_s`
- [ ] mp3: picks the highest bitrate from `[192, 128, 96, 64]` kbps where `duration_s * kbps * 1000 / 8 <= limit_bytes`; raises `TooLargeError` if even 64 kbps does not fit
- [ ] mp4: `height_ladder` is `[1080, 720, 480, 360, 240]`; a helper `format_selector(plan, height) -> str` returns a yt-dlp format string capping height and filtering by `filesize<=limit` / `filesize_approx<=limit`, merging video+audio, preferring mp4/m4a containers
- [ ] mp4 estimate: raises `TooLargeError` up front only if the *lowest* rung cannot plausibly fit (use `duration_s` and a documented conservative minimum bitrate constant, e.g. 240p ~ 300 kbps)
- [ ] Unit tests: each rejection case, bitrate selection at boundaries, `TooLargeError` for a very long mp3 at a small limit, and `format_selector` output for each height
- [ ] `./scripts/check.sh` passes

### US-004: Info embed builder
**Description:** As a user, I want an embed showing the video's key details alongside the file so I can see what was posted.

**Acceptance Criteria:**
- [ ] `build_embed(info: VideoInfo, kind: MediaKind) -> discord.Embed` in `src/ninja_bot/media/embed.py`
- [ ] Title is `info.title` (truncated to 256 chars) and links to `info.webpage_url`
- [ ] Fields: Uploader, Duration (via `format_duration`), Views (thousands-separated), Uploaded (`YYYY-MM-DD`); fields for missing data are omitted rather than shown as "None"
- [ ] Thumbnail is set only when `info.thumbnail` is present
- [ ] Footer shows the chosen format (`MP3` or `MP4`)
- [ ] Embed passes Discord limits (title <= 256, field values <= 1024) even for very long titles/uploader names
- [ ] Unit tests: full info, minimal info (only title + url), very long title, both kinds
- [ ] `./scripts/check.sh` passes

### US-005: yt-dlp downloader wrapper
**Description:** As a developer, I need one isolated class that talks to yt-dlp so the rest of the code can be tested with a fake.

**Acceptance Criteria:**
- [ ] `Downloader` `Protocol` in `src/ninja_bot/media/downloader.py` with `async fetch_info(url: str) -> VideoInfo` and `async download(url: str, plan: DownloadPlan, workdir: Path) -> Path`
- [ ] `YtDlpDownloader` is the only module importing `yt_dlp`; blocking calls run through `asyncio.to_thread`
- [ ] `fetch_info` uses `extract_info(url, download=False)` with `noplaylist=True`, `quiet=True`, `no_warnings=True`, then `parse_info`; yt-dlp `DownloadError` becomes our `DownloadError` with a friendly message
- [ ] A pure function `build_ydl_options(plan, format_height, workdir)` returns the yt-dlp options dict: mp3 uses `bestaudio/best` plus the `FFmpegExtractAudio` postprocessor (`preferredcodec: mp3`, `preferredquality` = plan bitrate); mp4 uses `format_selector(...)` with `merge_output_format: mp4`; both set `outtmpl` inside `workdir`, `noplaylist: True`, `max_filesize`/size guard, and `quiet: True`
- [ ] `download` walks the mp4 height ladder (or the single mp3 bitrate), checks the real output file size against `plan.limit_bytes`, steps down one rung when too big, and raises `TooLargeError` when nothing fits; leftover partial files in `workdir` are deleted between attempts
- [ ] Unit tests (no network) for `build_ydl_options` for both kinds and for the ladder/step-down logic using a stubbed yt-dlp call (inject the callable) and temp files of known size
- [ ] `./scripts/check.sh` passes

### US-006: `/download` slash command
**Description:** As a server member, I want to run `/download url:<link> format:<mp3|mp4>` and get the file plus an embed in the channel.

**Acceptance Criteria:**
- [ ] `src/ninja_bot/cogs/media.py` defines a `Media` cog with an `app_commands` command `download(url: str, format: Choice = mp4)`; `format` is optional, shown as a dropdown with `mp3` and `mp4`, default `mp4`
- [ ] Cog takes a `Downloader` (default `YtDlpDownloader`) and `max_duration_s` via constructor so tests inject a fake; `setup(bot)` builds it from `bot.config.max_duration_seconds`
- [ ] Flow: `validate_url` -> `interaction.response.defer(thinking=True)` -> `fetch_info` -> `plan_download` using `interaction.guild.filesize_limit` (fallback `10 * 1024 * 1024` when there is no guild) -> `download` into a `tempfile.TemporaryDirectory` -> `interaction.followup.send(embed=build_embed(...), file=discord.File(path))`
- [ ] `validate_url` failure is answered with an ephemeral message (before deferring) naming the problem
- [ ] Each `MediaError` subtype after deferring is answered via followup with a clear, friendly message; `TooLargeError` and `UnsupportedMediaError` messages include the embed when info is available; unexpected exceptions are logged and answered with a generic "something went wrong" message without leaking internals or tokens
- [ ] An `asyncio.Semaphore(1)` allows one download at a time (second request waits; user sees the thinking state)
- [ ] 30-second per-user cooldown via `app_commands.checks.cooldown(1, 30)`, with a handler that replies ephemerally with the remaining time
- [ ] Temp directory is removed in every outcome (success and each error path)
- [ ] `"ninja_bot.cogs.media"` added to `EXTENSIONS` in `src/ninja_bot/bot.py`
- [ ] Tests with a fake `Downloader` and fake `Interaction` cover: success mp4 (default), success mp3, invalid URL, each `MediaError` subtype, unexpected exception, guild limit vs no-guild fallback, temp dir cleanup
- [ ] `./scripts/check.sh` passes
- [ ] Note in `progress.txt`: "needs manual check in Discord test server" (cannot be verified by Ralph)

### US-007: Documentation
**Description:** As the owner, I want the docs to describe the new feature, its system dependencies and its upkeep.

**Acceptance Criteria:**
- [ ] `CLAUDE.md` architecture section lists `src/ninja_bot/media/` (models, planning, embed, downloader) and `cogs/media.py`
- [ ] `README.md` documents `/download` usage, `MAX_DURATION_SECONDS`, the ffmpeg requirement (`apt install ffmpeg` on the Debian 13 prod LXC) and the manual test checklist
- [ ] `README.md` notes yt-dlp upkeep: `uv lock --upgrade-package yt-dlp` when YouTube extractors break
- [ ] `./scripts/check.sh` passes

## Functional Requirements

- FR-1: The bot must provide a slash command `/download` with a required `url` string and an optional `format` choice of `mp3` or `mp4`, defaulting to `mp4`.
- FR-2: Any member of the server may use the command; no role restrictions.
- FR-3: Only `http`/`https` URLs are accepted; anything else is rejected before any network call.
- FR-4: The command must defer its response immediately because downloads exceed Discord's 3-second interaction window.
- FR-5: Only single videos are supported: playlists and live streams are rejected with a clear message (`noplaylist=True`).
- FR-6: Videos longer than `MAX_DURATION_SECONDS` (default 3600) are rejected.
- FR-7: The bot must target the invoking server's `filesize_limit` (fallback 10 MiB outside a guild) and pick the highest quality that fits: mp3 bitrate in `[192, 128, 96, 64]` kbps, mp4 height cap in `[1080, 720, 480, 360, 240]`.
- FR-8: After downloading, the real file size must be verified against the limit; if it is too large the bot steps down one quality rung and retries, finally refusing with `TooLargeError`.
- FR-9: On success the bot posts one message with the file attached and an embed containing title (linked), uploader, duration, views, upload date, thumbnail and chosen format; missing data is omitted.
- FR-10: Only one download may run at a time; each user has a 30-second cooldown.
- FR-11: Temporary files must be deleted after every request regardless of outcome.
- FR-12: Errors must produce friendly user-facing messages; internal details and exceptions are logged, never shown.
- FR-13: All yt-dlp usage is confined to `media/downloader.py`.

## Non-Goals (Out of Scope)

- Playlists, batch downloads, live streams, subtitles, or choosing resolution/bitrate manually.
- Hosting oversized files elsewhere (web server, cloud storage).
- Persisting history, caching downloads, or deduplicating requests.
- Role-based permissions, per-guild settings, or admin commands.
- Voice-channel playback.
- Automatic yt-dlp updating.

## Technical Considerations

- discord.py: use `app_commands.Choice`/`@app_commands.choices`; `Guild.filesize_limit` provides the per-server upload limit.
- yt-dlp breaks regularly when sites change; keep it behind `Downloader` and upgrade via `uv lock --upgrade-package yt-dlp`.
- ffmpeg must exist on `PATH` in the dev container and the prod LXC.
- Never block the event loop: all yt-dlp calls go through `asyncio.to_thread`.
- No automated test may hit the network; fake the downloader and stub the yt-dlp call.
- Follow CLAUDE.md conventions: thin cogs, pure logic in `media/`, mypy strict, Conventional Commits.

## Success Metrics

- `/download` on a short public video returns a playable mp4 or mp3 with a correct embed in the dev test server.
- A video that cannot fit is refused with a clear message, not a Discord upload error.
- `./scripts/check.sh` is green and no test uses the network.
- No leftover files in the temp directory after any run.

## Open Questions

- None blocking. Possible follow-ups: configurable cooldown, a `/formats` preview, hosting oversized files from the homelab.
