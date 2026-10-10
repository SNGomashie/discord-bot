from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from ninja_bot.media.models import DownloadError, TooLargeError, VideoInfo, parse_info
from ninja_bot.media.planning import DownloadPlan, format_selector

log = logging.getLogger(__name__)

# Blocking yt-dlp calls, injectable so tests never touch the network.
ExtractFn = Callable[[str, dict[str, Any]], dict[str, Any]]
RunFn = Callable[[str, dict[str, Any]], None]

_BASE_OPTIONS: dict[str, Any] = {"noplaylist": True, "quiet": True, "no_warnings": True}

# Finished media files yt-dlp might leave behind (not .part/.ytdl temp files).
_MEDIA_SUFFIXES = frozenset(
    {".mp4", ".webm", ".mkv", ".mov", ".flv", ".m4a", ".mp3", ".opus", ".ogg", ".aac", ".wav"}
)


class Downloader(Protocol):
    async def fetch_info(self, url: str) -> VideoInfo: ...

    async def download(self, url: str, plan: DownloadPlan, workdir: Path) -> Path: ...


def build_ydl_options(plan: DownloadPlan, height: int | None, workdir: Path) -> dict[str, Any]:
    """yt-dlp options for one download attempt (`height` is required for mp4)."""
    options: dict[str, Any] = {
        **_BASE_OPTIONS,
        "outtmpl": str(workdir / "%(title).100B.%(ext)s"),
        "restrictfilenames": True,
    }
    if plan.kind == "mp3":
        if plan.audio_bitrate_kbps is None:
            raise ValueError("mp3 plan needs audio_bitrate_kbps")
        options["format"] = "bestaudio/best"
        options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(plan.audio_bitrate_kbps),
            }
        ]
    else:
        if height is None:
            raise ValueError("mp4 download needs a height")
        options["format"] = format_selector(plan, height)
        options["merge_output_format"] = "mp4"
        # A single-file best format can still be webm/mkv; remux it into mp4.
        options["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]
    if plan.clip is not None:
        if plan.clip.end_s is None:
            raise ValueError("clip must be resolved (end_s set) before downloading")
        options["download_ranges"] = _download_ranges(plan.clip.start_s, plan.clip.end_s)
        options["force_keyframes_at_cuts"] = True
    return options


def _download_ranges(start_s: float, end_s: float) -> Any:
    import yt_dlp.utils

    return yt_dlp.utils.download_range_func(None, [(start_s, end_s)])


def _ytdlp_extract(url: str, options: dict[str, Any]) -> dict[str, Any]:
    import yt_dlp

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    return dict(ydl.sanitize_info(info))


def _ytdlp_run(url: str, options: dict[str, Any]) -> None:
    import yt_dlp

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])


def _is_ytdlp_error(exc: Exception) -> bool:
    import yt_dlp.utils

    return isinstance(exc, yt_dlp.utils.DownloadError)


class YtDlpDownloader:
    """The only code that talks to yt-dlp; blocking calls run in a worker thread."""

    def __init__(
        self,
        extract: ExtractFn = _ytdlp_extract,
        run: RunFn = _ytdlp_run,
        is_ytdlp_error: Callable[[Exception], bool] = _is_ytdlp_error,
    ) -> None:
        self._extract = extract
        self._run = run
        self._is_ytdlp_error = is_ytdlp_error

    async def fetch_info(self, url: str) -> VideoInfo:
        try:
            raw = await asyncio.to_thread(self._extract, url, dict(_BASE_OPTIONS))
        except Exception as exc:
            if self._is_ytdlp_error(exc):
                log.info("yt-dlp could not fetch info for %s: %s", url, exc)
                raise DownloadError(
                    "I couldn't read that link. It may be private, removed or unsupported."
                ) from exc
            raise
        return parse_info(raw)

    async def download(self, url: str, plan: DownloadPlan, workdir: Path) -> Path:
        """Download, stepping down the mp4 height ladder until the file fits the limit."""
        heights: tuple[int | None, ...] = plan.height_ladder if plan.kind == "mp4" else (None,)
        for height in heights:
            before = await asyncio.to_thread(_snapshot, workdir)
            options = build_ydl_options(plan, height, workdir)
            try:
                await asyncio.to_thread(self._run, url, options)
            except Exception as exc:
                await asyncio.to_thread(_remove_new_files, workdir, before)
                if self._is_ytdlp_error(exc):
                    log.info("yt-dlp download failed for %s at %s: %s", url, height, exc)
                    raise DownloadError() from exc
                raise
            output = await asyncio.to_thread(_find_output, workdir, before, plan)
            if (
                output is not None
                and await asyncio.to_thread(_file_size, output) <= plan.limit_bytes
            ):
                await asyncio.to_thread(_remove_new_files, workdir, before, output)
                return output
            unexpected = (
                await asyncio.to_thread(_unexpected_suffixes, workdir, before)
                if output is None
                else []
            )
            await asyncio.to_thread(_remove_new_files, workdir, before)
            if output is None:
                if unexpected:
                    log.info("%s produced %s instead of .%s", url, ", ".join(unexpected), plan.kind)
                    raise DownloadError(
                        f"The download produced a {unexpected[0]} file instead of"
                        f" .{plan.kind}, so I couldn't post it."
                    )
                raise DownloadError("The download finished but produced no file.")
            log.info("%s at height %s is too large, stepping down", url, height)
        raise TooLargeError("Even the lowest quality is too large for this server's upload limit.")


def _find_output(workdir: Path, before: set[Path], plan: DownloadPlan) -> Path | None:
    new = [p for p in workdir.iterdir() if p not in before and p.is_file()]
    matches = [p for p in new if p.suffix == f".{plan.kind}"]
    return max(matches, key=lambda p: p.stat().st_size, default=None)


def _unexpected_suffixes(workdir: Path, before: set[Path]) -> list[str]:
    """Sorted suffixes of new media files (used when none match the plan's extension)."""
    new = [p for p in workdir.iterdir() if p not in before and p.is_file()]
    return sorted({p.suffix for p in new if p.suffix in _MEDIA_SUFFIXES})


def _remove_new_files(workdir: Path, before: set[Path], keep: Path | None = None) -> None:
    for path in workdir.iterdir():
        if path not in before and path != keep and path.is_file():
            path.unlink(missing_ok=True)


def _file_size(path: Path) -> int:
    return path.stat().st_size


def _snapshot(workdir: Path) -> set[Path]:
    return set(workdir.iterdir())
