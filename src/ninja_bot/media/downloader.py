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
    return options


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
            before = _snapshot(workdir)
            options = build_ydl_options(plan, height, workdir)
            try:
                await asyncio.to_thread(self._run, url, options)
            except Exception as exc:
                _remove_new_files(workdir, before)
                if self._is_ytdlp_error(exc):
                    log.info("yt-dlp download failed for %s at %s: %s", url, height, exc)
                    raise DownloadError() from exc
                raise
            output = _find_output(workdir, before, plan)
            if output is not None and output.stat().st_size <= plan.limit_bytes:
                _remove_new_files(workdir, before, keep=output)
                return output
            _remove_new_files(workdir, before)
            if output is None:
                raise DownloadError("The download finished but produced no file.")
            log.info("%s at height %s is too large, stepping down", url, height)
        raise TooLargeError("Even the lowest quality is too large for this server's upload limit.")


def _find_output(workdir: Path, before: set[Path], plan: DownloadPlan) -> Path | None:
    new = [p for p in workdir.iterdir() if p not in before and p.is_file()]
    matches = [p for p in new if p.suffix == f".{plan.kind}"]
    return max(matches, key=lambda p: p.stat().st_size, default=None)


def _remove_new_files(workdir: Path, before: set[Path], keep: Path | None = None) -> None:
    for path in workdir.iterdir():
        if path not in before and path != keep and path.is_file():
            path.unlink(missing_ok=True)


def _snapshot(workdir: Path) -> set[Path]:
    return set(workdir.iterdir())
