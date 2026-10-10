# PRD: Harden `/download` (code review follow-ups)

## Introduction

A code review of the merged `/download` and clip features found nine issues. The most important one makes the headline clip use case fail (a short clip of a long video is rejected by a whole-video size filter). The rest are smaller correctness, fairness and robustness problems. This PRD fixes all nine without adding user-facing features. Every fix keeps the existing behaviour for inputs that already worked.

## Goals

- A short clip of a long video downloads successfully on the base 10 MiB upload limit.
- Invalid input never raises an unfriendly error or burns the user's cooldown.
- The quality ladder never re-downloads the same format.
- An "mp4" request never fails just because the source only offered webm/mkv.
- Queued requests give feedback and cannot outlive their Discord interaction token.
- Blocking disk work no longer runs on the event loop.
- The dev image's deno version is pinned.

## User Stories

### US-001: Don't apply the whole-video size filter to clips
**Description:** As a user, I want a short clip of a long video to download so that clipping works on long videos.

**Acceptance Criteria:**
- [ ] In `planning.py`, `format_selector(plan, height)` omits the `filesize<=?` / `filesize_approx<=?` filters when `plan.clip` is set (the clip's real size is verified after download by the existing check in `downloader.py`)
- [ ] Without a clip the selector output is byte-for-byte unchanged (existing tests keep passing)
- [ ] The docstring of `format_selector` explains why clips skip the size filter
- [ ] Unit tests: selector with a clip has no `filesize` text for each ladder height; selector without a clip is unchanged
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-002: Make timestamp parsing safe against huge input
**Description:** As a user, I want absurd `start`/`end` values to give the friendly clip error instead of a crash.

**Acceptance Criteria:**
- [ ] `parse_timestamp` raises `InvalidClipError` when the trimmed input is longer than 32 characters, before any `int()`/`float()` conversion
- [ ] `parse_timestamp` never raises anything but `InvalidClipError` for any string input (e.g. 5000 digits, 5000 digits followed by `m`, `1e400`, `inf`, `nan`, unicode digits)
- [ ] `start` and `end` on `/download` are typed `app_commands.Range[str, 0, 32]`-style (max length 32) or equivalent so Discord enforces the bound client-side; `start`/`end` still default to `None` and stay optional
- [ ] Unit tests for each oversize/odd input above and for the option's max length
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-003: Use the exact video duration when resolving clips
**Description:** As a user, I want clips near the very end of a video to work, so I don't get "past the end" for a video that really has those seconds.

**Acceptance Criteria:**
- [ ] `VideoInfo` gains `duration_exact_s: float | None = None`, filled by `parse_info` from yt-dlp's `duration` (kept as float); `duration_s` stays the truncated int used for display and existing behaviour
- [ ] `resolve_clip` accepts the exact `float` duration and uses it for the "start past the end", clamping and "at least 1 second" checks; `plan_download` passes `info.duration_exact_s` when present, else `float(info.duration_s)`
- [ ] A video of 100.9 s: `start=100.2` is rejected only if the clip would be under 1 s, `start=50` with no end yields a clip ending at 100.9
- [ ] Display of video duration (embed, error messages) is unchanged
- [ ] Unit tests for `parse_info` with float durations, `resolve_clip` with float durations at the edges, and `plan_download` end-to-end with a 100.9 s video
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-004: Only walk quality rungs that differ
**Description:** As the owner, I want the mp4 ladder to skip rungs that resolve to the same format so the LXC doesn't re-download and re-encode identical content.

**Acceptance Criteria:**
- [ ] `VideoInfo` gains `video_heights: tuple[int, ...] = ()` with the distinct heights of the video formats yt-dlp reports (`formats[*].height` where `vcodec` is not `none`), ascending; empty when unknown
- [ ] `parse_info` fills it, ignoring missing/zero/non-int heights
- [ ] `plan_download` builds `height_ladder` for mp4 as follows: if `video_heights` is empty use `HEIGHT_LADDER` unchanged; otherwise map each rung to the largest available height <= that rung (skip rungs with none) and de-duplicate while keeping descending order (e.g. available 144/360/480 -> `(480, 360)`; rung 240 maps to 144 -> `(480, 360, 144)`)
- [ ] If no available height is <= the lowest rung but heights exist, the ladder contains the smallest available height
- [ ] The downloader and `format_selector` need no changes beyond receiving the shorter ladder
- [ ] Unit tests: unknown heights (unchanged ladder), exact matches, collapsing rungs, only-small video, only-large video (smallest available is used), `parse_info` height extraction
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-005: Always produce a real mp4 for mp4 requests
**Description:** As a user, I want an mp4 request to return an mp4 even when the best source format is webm or mkv.

**Acceptance Criteria:**
- [ ] `build_ydl_options` for mp4 plans adds a `FFmpegVideoRemuxer` postprocessor with `preferedformat: "mp4"` (yt-dlp's spelling) so non-mp4 outputs are remuxed
- [ ] mp3 options are unchanged
- [ ] `_find_output` keeps matching only the plan's extension, but when no matching file exists and other new media files do, `download` raises `DownloadError` with a clear message and logs the unexpected extensions at INFO (no silent "no file" message)
- [ ] Unit tests: options contain the remuxer for mp4 and not for mp3; `_find_output`/`download` behaviour for a new `.webm` file only (error with message), for `.mp4` (success)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-006: Start the cooldown only after valid input
**Description:** As a user, I want a typo to not lock me out for 30 seconds.

**Acceptance Criteria:**
- [ ] New small class `UserCooldown` (in `cogs/media.py` or `media/cooldown.py`) with an injectable clock: `try_acquire(user_id) -> float` returns 0.0 and records the time when allowed, else the remaining seconds; `refund(user_id)` clears the user's record
- [ ] The `@app_commands.checks.cooldown` decorator is removed from `/download`; `handle_download` validates URL and clip first, then calls `try_acquire(interaction.user.id)` and replies ephemerally with the existing `cooldown_message` when blocked
- [ ] The cooldown is refunded when the request ends without posting a file (any `MediaError` or unexpected exception after the cooldown was taken), so a failed request can be retried immediately; a successful post keeps the 30-second cooldown
- [ ] Invalid URL or invalid clip replies never consume the cooldown
- [ ] `cog_app_command_error` no longer needs the `CommandOnCooldown` branch (remove it); other errors still produce the generic reply
- [ ] Existing cooldown tests are replaced by tests for `UserCooldown` (allowed, blocked, remaining time, refund, different users independent) and for the cog flow (invalid input doesn't consume, success consumes, failure refunds)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-007: Bounded, visible queue for downloads
**Description:** As a user, I want to know when my request is waiting behind another download, and not lose it silently if the wait is too long.

**Acceptance Criteria:**
- [ ] When `/download` is deferred and the semaphore is already held, the bot edits the original response to a short "queued behind another download" message (`interaction.edit_original_response`)
- [ ] Waiting for the semaphore is limited by `QUEUE_TIMEOUT_SECONDS = 300` (module constant, `asyncio.wait_for` on `semaphore.acquire()`); on timeout the user gets a friendly "the bot is busy, try again in a few minutes" followup, the cooldown is refunded, and no download runs
- [ ] The semaphore is always released after a download, including when the download raises
- [ ] All `interaction.followup.send` / `edit_original_response` calls on the deferred path are wrapped so a `discord.HTTPException` (e.g. expired token) is logged and swallowed instead of propagating; the temp dir is still cleaned up
- [ ] Tests with the fake interaction and fake downloader: queued message appears only when the semaphore is held; timeout path (patch the timeout constant to a tiny value); semaphore released after success and after an exception; an `HTTPException` from followup is swallowed and logged
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-008: Keep disk work off the event loop
**Description:** As the owner, I want large-file cleanup not to stall the bot's heartbeat.

**Acceptance Criteria:**
- [ ] `_download_deferred` creates its work directory with `tempfile.mkdtemp` via `asyncio.to_thread` and removes it with `shutil.rmtree(..., ignore_errors=True)` via `asyncio.to_thread` in a `finally`, replacing the synchronous `TemporaryDirectory` context manager
- [ ] In `downloader.py`, `_snapshot`, `_find_output`, `_remove_new_files` and the output `stat()` calls run through `asyncio.to_thread` when called from `download`
- [ ] The temp directory is removed in every outcome (success, each `MediaError`, unexpected exception, `HTTPException` while sending)
- [ ] Existing behaviour and tests keep passing; tests that assert cleanup are updated to the new mechanism
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-009: Pin deno and update docs
**Description:** As the owner, I want reproducible dev images and docs that match the new behaviour.

**Acceptance Criteria:**
- [ ] `Dockerfile.dev` copies deno from a pinned tag: `COPY --from=denoland/deno:bin-2.9.7 /deno /usr/local/bin/deno` (2.9.7 is the version verified with yt-dlp 2026.8.19)
- [ ] README mentions the pinned deno version next to the deno requirement and says to bump it deliberately when updating yt-dlp
- [ ] README `/download` section notes: clips skip the whole-video size filter, queued requests show a message and time out after 5 minutes, and the cooldown only starts after a valid request
- [ ] CLAUDE.md "yt-dlp gotchas" gains: mp4 requests are remuxed to mp4; the ladder only contains distinct heights
- [ ] Add a note to progress.txt: "needs docker rebuild check for pinned deno tag" (not verifiable by Ralph)
- [ ] `./scripts/check.sh` passes

## Functional Requirements

- FR-1: Clip downloads must not be filtered by whole-video size; the post-download size check is the authority.
- FR-2: `parse_timestamp` raises only `InvalidClipError`; inputs over 32 characters are rejected before numeric conversion.
- FR-3: Clip resolution uses the video's exact (float) duration; displayed durations are unchanged.
- FR-4: The mp4 ladder contains only distinct, actually available heights when the format list is known; otherwise the fixed ladder.
- FR-5: mp4 requests are remuxed to mp4; unexpected output types produce a clear error.
- FR-6: The 30-second per-user cooldown starts only after URL and clip validation pass, and is refunded if nothing was posted.
- FR-7: Waiting for the download slot shows a queued message, times out after 300 seconds with a friendly reply, and never raises out of the handler.
- FR-8: Directory creation/removal and output-file scans run in worker threads.
- FR-9: `Dockerfile.dev` pins the deno image tag.

## Non-Goals (Out of Scope)

- Multiple concurrent downloads, persistent queues, or queue position numbers.
- Changing the quality ladder values, bitrate list, or default limits.
- New user-facing options or commands.
- Per-guild settings or role restrictions.
- Caching downloads between requests.

## Technical Considerations

- Follow CLAUDE.md: pure logic in `media/`, thin cog, mypy strict, Conventional Commits (one commit per story, story ID in the body).
- No test may touch the network or run ffmpeg; yt-dlp and Discord objects are faked.
- Pattern notes in `progress.txt` (cog tests call `.callback` or `handle_*`; ruff ASYNC240 flags pathlib calls inside async functions) still apply.
- Behaviour decisions made here: cooldown is refunded on failures; queue timeout is 5 minutes; a longer clip than `MAX_DURATION_SECONDS` is still rejected (unchanged).

## Success Metrics

- A 20-second clip of a 40-minute video succeeds on the 10 MiB limit in the dev test server.
- A mistyped `end` or URL no longer triggers "Slow down!" on the retry.
- A video offering a single height triggers exactly one download attempt.
- `./scripts/check.sh` is green and no test uses the network.

## Open Questions

- None blocking.
