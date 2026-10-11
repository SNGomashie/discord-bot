import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "capture_leetify.py"
REAL_ID = "76561198012345678"
FRIEND_ID = "76561198087654321"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_leetify", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["capture_leetify"] = module
    spec.loader.exec_module(module)
    return module


capture = load_script()


def documents() -> dict[str, Any]:
    return {
        "profile": {
            "steam64_id": REAL_ID,
            "name": "RealName",
            "recent_matches": [{"id": "m1", "map_name": "de_mirage"}],
        },
        "match": {
            "id": "m1",
            "stats": [
                {"steam64_id": REAL_ID, "name": "RealName", "total_kills": 20},
                {"steam64_id": int(FRIEND_ID), "name": "Friend", "total_kills": 9},
            ],
            "mvp": FRIEND_ID,
        },
    }


def test_anonymize_replaces_ids_and_names_consistently() -> None:
    result = capture.anonymize(documents(), REAL_ID)
    text = json.dumps(result)
    assert REAL_ID not in text
    assert FRIEND_ID not in text
    assert "RealName" not in text
    assert "Friend" not in text
    own = result["profile"]["steam64_id"]
    assert result["match"]["stats"][0]["steam64_id"] == own
    assert result["match"]["stats"][0]["name"] == result["profile"]["name"]
    friend = result["match"]["stats"][1]["steam64_id"]
    assert friend != own
    assert result["match"]["mvp"] == friend  # known IDs under other keys too


def test_anonymize_keeps_other_values_and_valid_ids() -> None:
    result = capture.anonymize(documents(), REAL_ID)
    assert result["profile"]["recent_matches"] == [{"id": "m1", "map_name": "de_mirage"}]
    assert result["match"]["stats"][0]["total_kills"] == 20
    for player in result["match"]["stats"]:
        assert capture.parse_steam_id(player["steam64_id"]) == player["steam64_id"]


def test_anonymize_does_not_mutate_input() -> None:
    docs = documents()
    capture.anonymize(docs, REAL_ID)
    assert docs == documents()


def test_latest_match_id() -> None:
    matches = [
        {"id": "old", "finished_at": "2026-01-01T10:00:00Z"},
        {"id": "new", "finished_at": "2026-02-01T10:00:00Z"},
        {"finished_at": "2027-01-01T10:00:00Z"},
        "junk",
        {"id": "undated"},
    ]
    assert capture.latest_match_id(matches) == "new"
    assert capture.latest_match_id([]) is None
    assert capture.latest_match_id({"id": "x"}) is None


def test_write_documents(tmp_path: Path) -> None:
    paths = capture.write_documents({"profile": {"name": "é"}}, tmp_path / "out")
    assert paths == [tmp_path / "out" / "profile.json"]
    assert json.loads(paths[0].read_text(encoding="utf-8")) == {"name": "é"}


def test_output_dir_is_gitignored_data() -> None:
    assert capture.OUTPUT_DIR.parent.name == "data"


async def test_refuses_outside_dev(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(capture, "load_dotenv", lambda: None)
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("LEETIFY_API_KEY", "secret-key")
    assert await capture.main([REAL_ID]) == 1
    assert "ENVIRONMENT=dev" in capsys.readouterr().out


async def test_requires_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(capture, "load_dotenv", lambda: None)
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("LEETIFY_API_KEY", " ")
    assert await capture.main([REAL_ID]) == 1
    assert "LEETIFY_API_KEY" in capsys.readouterr().out


async def test_rejects_bad_steam_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture, "load_dotenv", lambda: None)
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("LEETIFY_API_KEY", "secret-key")
    assert await capture.main(["not-an-id"]) == 2
    assert await capture.main([]) == 2


async def test_failure_never_prints_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def failing(api_key: str, steam64_id: str) -> dict[str, Any]:
        raise capture.CaptureError("/v3/profile: HTTP 401")

    monkeypatch.setattr(capture, "load_dotenv", lambda: None)
    monkeypatch.setattr(capture, "capture", failing)
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("LEETIFY_API_KEY", "secret-key")
    assert await capture.main([REAL_ID]) == 1
    out = capsys.readouterr().out
    assert "HTTP 401" in out
    assert "secret-key" not in out


async def test_success_writes_anonymized_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_capture(api_key: str, steam64_id: str) -> dict[str, Any]:
        assert api_key == "secret-key"
        return documents()

    monkeypatch.setattr(capture, "load_dotenv", lambda: None)
    monkeypatch.setattr(capture, "capture", fake_capture)
    monkeypatch.setattr(capture, "OUTPUT_DIR", tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("LEETIFY_API_KEY", "secret-key")
    assert await capture.main([f"https://steamcommunity.com/profiles/{REAL_ID}"]) == 0
    written = (tmp_path / "match.json").read_text(encoding="utf-8")
    assert REAL_ID not in written and "RealName" not in written
    assert (tmp_path / "profile.json").exists()
    out = capsys.readouterr().out
    assert "secret-key" not in out
    assert f"Result: {capture.COMPARE_UNKNOWN}" in out
    assert REAL_ID not in out and "RealName" not in out


def player(steam64_id: str, name: str, team: int | None) -> dict[str, Any]:
    return {"steam64_id": steam64_id, "name": name, "initial_team_number": team}


def full_stats() -> list[dict[str, Any]]:
    ids = [f"7656119809{i:07d}" for i in range(9)]
    others = [player(sid, f"Real{i}", 2 if i < 4 else 3) for i, sid in enumerate(ids)]
    return [player(REAL_ID, "RealName", 2), *others]


def capture_docs(history_stats: list[dict[str, Any]], match: Any = None) -> dict[str, Any]:
    docs: dict[str, Any] = {
        "profile": {"steam64_id": REAL_ID, "name": "RealName"},
        "matches": [
            {"id": "real-match-uuid", "finished_at": "2026-02-01", "stats": history_stats},
            {"id": "older-match-uuid", "finished_at": "2026-01-01", "stats": history_stats},
        ],
    }
    if match is not None:
        docs["match"] = match
    return docs


def assert_safe(lines: list[str]) -> None:
    text = "\n".join(lines)
    for secret in (REAL_ID, "RealName", "Real1", "7656119809", "real-match-uuid", "secret"):
        assert secret not in text


def test_summarize_both_endpoints_full() -> None:
    docs = capture_docs(full_stats(), {"id": "real-match-uuid", "stats": full_stats()})
    lines = capture.summarize_documents(capture.anonymize(docs, REAL_ID))
    assert lines == [
        "history match-1: 10 players in stats, 2 distinct team numbers, 0 without a team number",
        "history match-2: 10 players in stats, 2 distinct team numbers, 0 without a team number",
        "single match match-1: 10 players in stats, 2 distinct team numbers, "
        "0 without a team number",
        f"Result: {capture.COMPARE_SAME}",
    ]
    assert_safe(lines)


def test_summarize_history_with_one_player() -> None:
    own = [player(REAL_ID, "RealName", 2)]
    docs = capture_docs(own, {"id": "real-match-uuid", "stats": full_stats()})
    lines = capture.summarize_documents(capture.anonymize(docs, REAL_ID))
    assert lines[0] == (
        "history match-1: 1 players in stats, 1 distinct team numbers, 0 without a team number"
    )
    assert lines[2].startswith("single match match-1: 10 players")
    assert lines[-1] == f"Result: {capture.COMPARE_FEWER}"
    assert_safe(lines)


def test_summarize_missing_single_match() -> None:
    docs = capture_docs([player(REAL_ID, "RealName", 2)])
    lines = capture.summarize_documents(capture.anonymize(docs, REAL_ID))
    assert "single match: not fetched" in lines
    assert lines[-1] == f"Result: {capture.COMPARE_UNKNOWN}"
    assert_safe(lines)


def test_summarize_null_team_numbers() -> None:
    stats = full_stats()
    stats[1]["initial_team_number"] = None
    del stats[2]["initial_team_number"]
    docs = capture_docs(stats, {"id": "real-match-uuid", "stats": stats})
    lines = capture.summarize_documents(capture.anonymize(docs, REAL_ID))
    assert lines[2] == (
        "single match match-1: 10 players in stats, 2 distinct team numbers, "
        "2 without a team number"
    )
    assert lines[-1] == f"Result: {capture.COMPARE_SAME}"
    assert_safe(lines)


def test_summarize_never_copies_document_values_even_unanonymized() -> None:
    docs = capture_docs([player(REAL_ID, "RealName", 2)], {"id": "real-match-uuid"})
    lines = capture.summarize_documents(docs)
    assert "single match match-1: no stats list" in lines
    assert lines[-1] == f"Result: {capture.COMPARE_UNKNOWN}"
    assert_safe(lines)


def test_summarize_unmatched_ids_and_bad_shapes() -> None:
    other = {"id": "another-uuid", "stats": full_stats()}
    lines = capture.summarize_documents(capture_docs(full_stats(), other))
    assert lines[2].startswith("single match match-3:")
    assert lines[-1] == f"Result: {capture.COMPARE_UNKNOWN}"
    assert capture.summarize_documents({"matches": {}, "match": []}) == [
        "history: not a JSON list",
        "single match: not a JSON object",
        f"Result: {capture.COMPARE_UNKNOWN}",
    ]
    assert capture.summarize_documents({"matches": []})[0] == "history: no entries"
