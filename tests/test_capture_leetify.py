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
    assert "secret-key" not in capsys.readouterr().out
