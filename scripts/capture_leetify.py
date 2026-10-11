"""Capture real Leetify responses to check the hand-written test fixtures against.

Fetches the profile, the recent matches list and the latest match's details for one
SteamID64, replaces every `steam64_id` and `name` value with a placeholder, and writes
the JSON to data/leetify-capture/ (gitignored). Then prints how many players each match
lists, to show whether the history endpoint returns every player or only the requested
one. Needs LEETIFY_API_KEY and ENVIRONMENT=dev (from the environment or .env). The key
is never printed.

Not part of scripts/check.sh (needs network + key). Run manually:
    uv run python scripts/capture_leetify.py <steam64_id>
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp
from dotenv import load_dotenv

from ninja_bot.cs2.client import DEFAULT_BASE_URL
from ninja_bot.cs2.models import InvalidSteamIdError
from ninja_bot.cs2.steam import parse_steam_id

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "leetify-capture"
STEAM_ID_KEYS = frozenset({"steam64_id"})
NAME_KEYS = frozenset({"name"})
TIMEOUT_SECONDS = 20


class CaptureError(Exception):
    """A request failed; the message is safe to print (never contains the key)."""


class Anonymizer:
    """Maps real Steam IDs and names to stable placeholders, shared across files."""

    def __init__(self) -> None:
        self.steam_ids: dict[str, str] = {}
        self.names: dict[str, str] = {}

    def steam_id(self, real: str) -> str:
        if real not in self.steam_ids:
            self.steam_ids[real] = f"7656119800{len(self.steam_ids) + 1:07d}"
        return self.steam_ids[real]

    def name(self, real: str) -> str:
        if real not in self.names:
            self.names[real] = f"player-{len(self.names) + 1}"
        return self.names[real]

    def collect(self, value: Any) -> None:
        """Register every Steam ID and name in `value` (call on all data before `apply`)."""
        if isinstance(value, dict):
            for key, item in value.items():
                if key in STEAM_ID_KEYS and isinstance(item, str | int):
                    self.steam_id(str(item))
                elif key in NAME_KEYS and isinstance(item, str):
                    self.name(item)
                else:
                    self.collect(item)
        elif isinstance(value, list):
            for item in value:
                self.collect(item)

    def apply(self, value: Any) -> Any:
        """Return a copy with placeholders, including known Steam IDs under other keys."""
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if key in STEAM_ID_KEYS and isinstance(item, str | int):
                    result[key] = self.steam_id(str(item))
                elif key in NAME_KEYS and isinstance(item, str):
                    result[key] = self.name(item)
                else:
                    result[key] = self.apply(item)
            return result
        if isinstance(value, list):
            return [self.apply(item) for item in value]
        if isinstance(value, str) and value in self.steam_ids:
            return self.steam_ids[value]
        return value


def anonymize(documents: dict[str, Any], steam64_id: str) -> dict[str, Any]:
    """Anonymize several JSON documents with one shared mapping (`steam64_id` comes first)."""
    anonymizer = Anonymizer()
    anonymizer.steam_id(steam64_id)
    anonymizer.collect(documents)
    return {key: anonymizer.apply(doc) for key, doc in documents.items()}


def latest_match_id(matches: Any) -> str | None:
    """Id of the newest entry (by ISO `finished_at`) in the matches list, if any."""
    if not isinstance(matches, list):
        return None
    entries = [m for m in matches if isinstance(m, dict) and m.get("id") is not None]
    if not entries:
        return None
    newest = max(entries, key=lambda m: str(m.get("finished_at") or ""))
    return str(newest["id"])


COMPARE_FEWER = "history endpoint returns only the requested player"
COMPARE_SAME = "both endpoints return all players"
COMPARE_UNKNOWN = "could not compare"


def _player_count(match: Any) -> int | None:
    stats = match.get("stats") if isinstance(match, dict) else None
    return len(stats) if isinstance(stats, list) else None


def _describe_match(label: str, match: Any, match_ids: dict[str, str]) -> str:
    """One summary line with counts only (never ids or names from the document)."""
    if not isinstance(match, dict):
        return f"{label}: not a JSON object"
    raw_id = match.get("id")
    if raw_id is None:
        match_label = "match without id"
    else:
        match_label = match_ids.setdefault(str(raw_id), f"match-{len(match_ids) + 1}")
    stats = match.get("stats")
    if not isinstance(stats, list):
        return f"{label} {match_label}: no stats list"
    players = [p for p in stats if isinstance(p, dict)]
    team_numbers = {p.get("initial_team_number") for p in players} - {None}
    unassigned = sum(1 for p in players if p.get("initial_team_number") is None)
    return (
        f"{label} {match_label}: {len(stats)} players in stats, "
        f"{len(team_numbers)} distinct team numbers, {unassigned} without a team number"
    )


def _compare(history: Any, single: Any) -> str:
    if not isinstance(history, list) or not isinstance(single, dict):
        return COMPARE_UNKNOWN
    single_id = single.get("id")
    entry = next(
        (
            m
            for m in history
            if isinstance(m, dict) and single_id is not None and m.get("id") == single_id
        ),
        None,
    )
    history_count = _player_count(entry)
    single_count = _player_count(single)
    if history_count is None or single_count is None:
        return COMPARE_UNKNOWN
    return COMPARE_FEWER if history_count < single_count else COMPARE_SAME


def summarize_documents(documents: dict[str, Any]) -> list[str]:
    """Player counts per captured match plus a verdict on the history endpoint.

    Match ids are replaced by `match-N` labels (the same label for the same id), and no
    other values from the documents are copied, so the summary is safe to print.
    """
    match_ids: dict[str, str] = {}
    lines: list[str] = []
    history = documents.get("matches")
    if isinstance(history, list):
        if not history:
            lines.append("history: no entries")
        for entry in history:
            lines.append(_describe_match("history", entry, match_ids))
    else:
        lines.append("history: not a JSON list")
    single = documents.get("match")
    if single is None:
        lines.append("single match: not fetched")
    else:
        lines.append(_describe_match("single match", single, match_ids))
    lines.append(f"Result: {_compare(history, single)}")
    return lines


async def fetch_json(
    session: aiohttp.ClientSession,
    api_key: str,
    path: str,
    params: dict[str, str] | None = None,
) -> Any:
    url = DEFAULT_BASE_URL + path + ("?" + urlencode(params) if params else "")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    try:
        async with session.get(url, headers=headers) as response:
            body = await response.text()
            status = response.status
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        raise CaptureError(f"{path}: request failed ({type(exc).__name__})") from None
    if not 200 <= status < 300:
        raise CaptureError(f"{path}: HTTP {status}")
    try:
        return json.loads(body)
    except ValueError:
        raise CaptureError(f"{path}: response is not JSON") from None


async def capture(api_key: str, steam64_id: str) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        params = {"steam64_id": steam64_id}
        documents: dict[str, Any] = {
            "profile": await fetch_json(session, api_key, "/v3/profile", params),
            "matches": await fetch_json(session, api_key, "/v3/profile/matches", params),
        }
        match_id = latest_match_id(documents["matches"])
        if match_id is None:
            print("No matches in /v3/profile/matches; skipping match details")
        else:
            path = f"/v2/matches/{quote(match_id, safe='')}"
            documents["match"] = await fetch_json(session, api_key, path)
    return documents


def write_documents(documents: dict[str, Any], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, doc in documents.items():
        path = output_dir / f"{name}.json"
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


async def main(argv: list[str]) -> int:
    load_dotenv()
    if os.environ.get("ENVIRONMENT") != "dev":
        print("Refusing to run outside ENVIRONMENT=dev")
        return 1
    if len(argv) != 1:
        print("Usage: uv run python scripts/capture_leetify.py <steam64_id>")
        return 2
    try:
        steam64_id = parse_steam_id(argv[0])
    except InvalidSteamIdError as exc:
        print(exc.message)
        return 2
    api_key = os.environ.get("LEETIFY_API_KEY", "").strip()
    if not api_key:
        print("LEETIFY_API_KEY is not set (get one at leetify.com/app/developer)")
        return 1
    try:
        documents = await capture(api_key, steam64_id)
    except CaptureError as exc:
        print(f"FAIL: {exc}")
        return 1
    anonymized = anonymize(documents, steam64_id)
    for path in write_documents(anonymized, OUTPUT_DIR):
        print(f"Wrote {path}")
    for line in summarize_documents(anonymized):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
