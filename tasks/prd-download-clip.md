# PRD: Clip a section of a video in `/download`

## Introduction

Extend the existing `/download` slash command with optional `start` and `end` options so a member can post just a section of a video, as either mp3 or mp4, instead of the whole thing (e.g. `/download url:<link> format:mp3 start:1:30 end:2:45`). Cutting is done by yt-dlp's section download plus ffmpeg, with accurate (re-encoded) cut points. The upload-size fitting, embed and error handling from the first feature all keep working and are based on the clip rather than the full video.

## Goals

- `/download` gains optional `start` and `end` string options; without them behaviour is unchanged.
- Accept flexible time formats: `90`, `90.5`, `1:30`, `1:02:05`, `1h2m3s`.
- Cuts are accurate to the requested times (`force_keyframes_at_cuts`).
- The duration cap (`MAX_DURATION_SECONDS`) and upload-size planning apply to the clip length, so a long video can be clipped.
- The embed shows what was clipped.
- All logic is pure and unit-tested with no network.

## User Stories

### US-001: Timestamp parsing and Clip model
**Description:** As a developer, I need to parse user-typed times and a start/end pair into a validated `Clip` so the rest of the code works with clean values.

**Acceptance Criteria:**
- [ ] New module `src/ninja_bot/media/clip.py`
- [ ] `InvalidClipError(MediaError)` added to `models.py` with a friendly default message that includes example formats
- [ ] `parse_timestamp(text: str) -> float` accepts (case-insensitive, whitespace-trimmed): plain seconds (`90`, `90.5`), `m:ss` / `m:ss.s`, `h:mm:ss` / `h:mm:ss.s` (seconds < 60 and, when hours are present, minutes < 60), and unit style `1h2m3s`, `2m`, `45s`, `1m30.5s` (units in h, m, s order, each optional, at least one)
- [ ] `parse_timestamp` raises `InvalidClipError` for empty, negative, non-numeric, repeated or mis-ordered units (`30s1m`), and out-of-range parts (`1:75`)
- [ ] Frozen dataclass `Clip` with `start_s: float` and `end_s: float | None` (None = to the end of the video)
- [ ] `parse_clip(start: str | None, end: str | None) -> Clip | None` returns None when both are None/blank; `start` alone gives `Clip(start, None)`; `end` alone raises `InvalidClipError` ("give a start time too"); `end <= start` raises `InvalidClipError`
- [ ] `format_timestamp(seconds: float) -> str` renders `m:ss` / `h:mm:ss`, with one decimal only when the value has a fractional part (`90` -> `1:30`, `90.5` -> `1:30.5`, `3725` -> `1:02:05`)
- [ ] Unit tests in `tests/test_media_clip.py` covering every accepted format, every rejection case above, `parse_clip` combinations, and `format_timestamp`
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes (ruff, ruff format, mypy strict, pytest)

### US-002: Resolve clips against the video and plan with them
**Description:** As a user, I want the clip checked against the real video length and the size/duration limits to apply to the clip so I can clip long videos.

**Acceptance Criteria:**
- [ ] `resolve_clip(clip: Clip, duration_s: int) -> Clip` in `clip.py`: raises `InvalidClipError` if `start_s >= duration_s` (message includes the video length via `format_duration`); a `None` end becomes `duration_s`; an `end_s` beyond the video is clamped to `duration_s`; raises `InvalidClipError` if the resulting clip is shorter than 1 second
- [ ] `DownloadPlan` in `planning.py` gains `clip: Clip | None = None` (always a resolved clip with a concrete `end_s` when set)
- [ ] `plan_download(info, kind, limit_bytes, max_duration_s, clip: Clip | None = None)`: with a clip, resolves it against `info.duration_s`, applies the `max_duration_s` cap to the clip length (a clip under the cap on a longer video is allowed; the error message mentions the clip), and uses the clip length for all size estimates (mp3 bitrate choice and the mp4 up-front `TooLargeError` check)
- [ ] Without a clip, `plan_download` behaves exactly as before (existing tests keep passing unchanged)
- [ ] Live streams, playlists and unknown duration are still rejected when a clip is given
- [ ] The returned plan carries the resolved clip in `plan.clip`
- [ ] Unit tests: start past end of video, end clamped, clip under the cap on a long video, clip over the cap, clip shorter than 1s, sizes based on clip length (e.g. a clip that fits where the full video would not), no-clip behaviour unchanged
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-003: Download only the clipped section
**Description:** As a developer, I need the yt-dlp options to cut the requested section accurately for both mp3 and mp4.

**Acceptance Criteria:**
- [ ] In `downloader.py`, `build_ydl_options` adds, when `plan.clip` is set: `download_ranges` built with `yt_dlp.utils.download_range_func(None, [(start_s, end_s)])` and `force_keyframes_at_cuts: True`; applies to both mp3 and mp4 plans
- [ ] When `plan.clip` is None the options are identical to before (no `download_ranges`, no `force_keyframes_at_cuts`)
- [ ] `yt_dlp` is still imported only inside `downloader.py` (lazily, as in the existing helpers)
- [ ] The post-download real-size check, ladder step-down and cleanup logic work unchanged on the clipped file
- [ ] Unit tests (no network): options with/without a clip for mp3 and mp4; calling the `download_ranges` callable yields a range whose `start_time`/`end_time` match the clip
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-004: Show the clip in the embed
**Description:** As a user, I want the embed to say which part of the video was posted.

**Acceptance Criteria:**
- [ ] `build_embed(info, kind, clip: Clip | None = None)` adds a `Clip` field when a clip is given, formatted like `1:30 → 2:45 (1:15)` using `format_timestamp` (start, end, and clip length via `format_duration`/`format_timestamp`)
- [ ] The existing Duration field still shows the full video length; without a clip the embed is unchanged
- [ ] The field respects the 1024-character limit helpers already in `embed.py`
- [ ] Unit tests: embed with a clip (whole seconds and fractional), without a clip (unchanged), and both formats
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-005: `start` and `end` options on `/download`
**Description:** As a server member, I want to pass `start` and optionally `end` to `/download` so I get just that part of the video.

**Acceptance Criteria:**
- [ ] `download` in `cogs/media.py` gains optional `start: str | None = None` and `end: str | None = None` parameters with `@app_commands.describe` text giving example formats (`1:30`, `90`, `1m30s`)
- [ ] `handle_download` calls `parse_clip(start, end)` before deferring; an `InvalidClipError` is answered with an ephemeral message (same as an invalid URL) and nothing is downloaded
- [ ] The parsed clip is passed to `plan_download(..., clip=clip)`; errors raised there (`InvalidClipError`, `UnsupportedMediaError`, `TooLargeError`) go through the existing `MediaError` reply path
- [ ] The success reply uses `build_embed(info, kind, plan.clip)`; error embeds are built without a clip
- [ ] The cooldown, one-at-a-time semaphore, temp-dir cleanup and generic-error behaviour are unchanged
- [ ] Tests with the fake Downloader/Interaction: no clip (unchanged), start only, start+end, end only (ephemeral error before defer), end before start, start beyond video (followup error), clip plan reaches the downloader (`plan.clip` set), embed contains the Clip field, command params `start`/`end` are optional strings
- [ ] Add a note to progress.txt: "needs manual check in Discord test server" (not verifiable by Ralph)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-006: Documentation
**Description:** As the owner, I want the docs to describe the clip options and how to test them.

**Acceptance Criteria:**
- [ ] README `/download` section documents `start` / `end`, accepted time formats, that `end` alone is invalid, that `end` is clamped to the video length, and that `MAX_DURATION_SECONDS` applies to the clip length
- [ ] README manual test checklist gains: mp3 clip, mp4 clip, start-only clip, `end` without `start`, start beyond the video, a clip from a video longer than the cap
- [ ] CLAUDE.md architecture section lists `media/clip.py`
- [ ] `./scripts/check.sh` passes

## Functional Requirements

- FR-1: `/download` accepts optional `start` and `end` string options in addition to `url` and `format`.
- FR-2: Times may be given as seconds (`90`, `90.5`), `m:ss`, `h:mm:ss` (optionally with decimal seconds), or unit style (`1h2m3s`, `2m`, `45s`).
- FR-3: `start` alone means "from start to the end of the video". `end` without `start` is rejected with a friendly message. `end` must be after `start`.
- FR-4: `start` at or after the end of the video is rejected; an `end` past the end of the video is clamped to the video length. A clip shorter than 1 second is rejected.
- FR-5: Syntax errors in `start`/`end` are reported ephemerally before any network call; checks that need the video length are reported after fetching info.
- FR-6: Cuts are accurate (`force_keyframes_at_cuts=True`) for both mp3 and mp4.
- FR-7: `MAX_DURATION_SECONDS` applies to the clip length when a clip is requested, and to the full video otherwise.
- FR-8: Bitrate/height selection and the up-front size estimate use the clip length; the real file size is still verified after download.
- FR-9: The success embed includes a `Clip` field (`start → end (length)`) when a clip was requested.
- FR-10: Behaviour without `start`/`end` is identical to before.

## Non-Goals (Out of Scope)

- Multiple clips or chapters in one command; stitching sections together.
- Fast, inaccurate (keyframe-only) cutting.
- Reading the start time out of the URL (`?t=90`).
- Frame-level editing, fades, or re-encoding settings beyond the existing size ladder.
- Slash-command autocomplete or time pickers.

## Technical Considerations

- yt-dlp: `yt_dlp.utils.download_range_func(None, [(start, end)])` + `force_keyframes_at_cuts`; requires ffmpeg (already in the dev image and prod LXC). Accurate cuts re-encode around the cut points, so mp4 clips take longer than whole-file copies.
- Follow CLAUDE.md conventions: pure logic in `media/`, thin cog, mypy strict, Conventional Commits.
- Output file discovery in `downloader.py` matches by extension, so any section suffix yt-dlp adds to the filename does not matter.
- No test may touch the network.

## Success Metrics

- `/download url format:mp3 start:1:00 end:1:30` returns a ~30-second mp3 with a correct Clip field in the dev test server.
- A clip from a video longer than 60 minutes works when the clip is within the cap.
- Invalid ranges produce clear messages and never start a download.
- `./scripts/check.sh` is green and no test uses the network.

## Open Questions

- None blocking. Possible follow-up: accept `?t=` / `&t=` timestamps from the URL as a default `start`.
