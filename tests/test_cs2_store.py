from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ninja_bot.cs2 import store as store_module
from ninja_bot.cs2.store import LinkStore, MatchLog, SqliteLinkStore

STEAM_A = "76561197960287930"
STEAM_B = "76561198000000001"


def _exists(path: Path) -> bool:
    return path.exists()


def _store(tmp_path: Path) -> SqliteLinkStore:
    return SqliteLinkStore(tmp_path / "links.sqlite3")


def test_sqlite_store_satisfies_protocol(tmp_path: Path) -> None:
    store: LinkStore = _store(tmp_path)
    assert isinstance(store, SqliteLinkStore)


async def test_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.set(1, STEAM_A)
    assert await store.get(1) == STEAM_A
    assert await store.all_links() == {1: STEAM_A}


async def test_missing_user(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert await store.get(42) is None
    assert await store.all_links() == {}
    assert await store.remove(42) is False


async def test_set_replaces_existing_link(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.set(1, STEAM_A)
    await store.set(1, STEAM_B)
    assert await store.get(1) == STEAM_B
    assert await store.all_links() == {1: STEAM_B}


async def test_two_users_may_link_same_steam_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.set(1, STEAM_A)
    await store.set(2, STEAM_A)
    assert await store.all_links() == {1: STEAM_A, 2: STEAM_A}


async def test_remove(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.set(1, STEAM_A)
    await store.set(2, STEAM_B)
    assert await store.remove(1) is True
    assert await store.get(1) is None
    assert await store.remove(1) is False
    assert await store.all_links() == {2: STEAM_B}


async def test_data_survives_new_instance(tmp_path: Path) -> None:
    await _store(tmp_path).set(1, STEAM_A)
    assert await _store(tmp_path).get(1) == STEAM_A


async def test_creates_missing_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "links.sqlite3"
    assert not _exists(path.parent)
    store = SqliteLinkStore(path)
    await store.set(1, STEAM_A)
    assert _exists(path)
    assert await store.get(1) == STEAM_A


async def test_blocking_calls_run_in_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real_to_thread = asyncio.to_thread

    async def recorder(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        calls.append(func.__name__)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", recorder)
    store = _store(tmp_path)
    await store.set(1, STEAM_A)
    await store.get(1)
    await store.all_links()
    await store.discord_ids_for([STEAM_A])
    await store.remove(1)
    assert calls == ["_set", "_get", "_all_links", "_discord_ids_for", "_remove"]


async def test_discord_ids_for_found_and_not_found(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.set(1, STEAM_A)
    await store.set(2, STEAM_B)
    assert await store.discord_ids_for([STEAM_A, "76561198999999999"]) == {STEAM_A: 1}
    assert await store.discord_ids_for({STEAM_A, STEAM_B}) == {STEAM_A: 1, STEAM_B: 2}
    assert await store.discord_ids_for(["76561198999999999"]) == {}


async def test_discord_ids_for_empty_input_runs_no_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def recorder(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        calls.append(func.__name__)
        raise AssertionError("no query expected")

    monkeypatch.setattr(asyncio, "to_thread", recorder)
    path = tmp_path / "links.sqlite3"
    assert await SqliteLinkStore(path).discord_ids_for([]) == {}
    assert calls == []
    assert not _exists(path)


async def test_discord_ids_for_duplicate_links_returns_lowest_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.set(30, STEAM_A)
    await store.set(5, STEAM_A)
    await store.set(17, STEAM_A)
    assert await store.discord_ids_for([STEAM_A, STEAM_A]) == {STEAM_A: 5}


async def test_discord_ids_for_more_than_one_chunk(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ids = [str(76561198000000000 + n) for n in range(1200)]
    for n in (0, 499, 500, 501, 1199):
        await store.set(n + 1, ids[n])
    result = await store.discord_ids_for(ids)
    assert result == {ids[n]: n + 1 for n in (0, 499, 500, 501, 1199)}


async def test_discord_ids_for_binds_hostile_ids_as_parameters(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hostile = "1' OR '1'='1\"; DROP TABLE links; --"
    await store.set(1, STEAM_A)
    await store.set(2, hostile)
    assert await store.discord_ids_for(["1' OR '1'='1"]) == {}
    assert await store.discord_ids_for([hostile, '") OR 1=1 --']) == {hostile: 2}
    assert await store.all_links() == {1: STEAM_A, 2: hostile}


def test_sqlite_store_satisfies_match_log(tmp_path: Path) -> None:
    log: MatchLog = _store(tmp_path)
    assert isinstance(log, SqliteLinkStore)


async def test_seeded_flag_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert await store.is_seeded(STEAM_A) is False
    await store.mark_seeded(STEAM_A)
    await store.mark_seeded(STEAM_A)
    assert await store.is_seeded(STEAM_A) is True
    assert await store.is_seeded(STEAM_B) is False


async def test_removed_link_keeps_seeded_flag(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.set(1, STEAM_A)
    await store.mark_seeded(STEAM_A)
    assert await store.remove(1) is True
    assert await store.is_seeded(STEAM_A) is True


async def test_filter_unposted_keeps_input_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.mark_posted(["b", "d"])
    assert await store.filter_unposted(["e", "d", "c", "b", "a"]) == ["e", "c", "a"]
    assert await store.filter_unposted(["b", "d"]) == []


async def test_filter_unposted_empty_input_runs_no_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def recorder(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no query expected")

    monkeypatch.setattr(asyncio, "to_thread", recorder)
    path = tmp_path / "links.sqlite3"
    store = SqliteLinkStore(path)
    assert await store.filter_unposted([]) == []
    await store.mark_posted([])
    assert not _exists(path)


async def test_filter_unposted_more_than_one_chunk(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ids = [f"match-{n}" for n in range(1200)]
    await store.mark_posted([ids[n] for n in (0, 499, 500, 501, 1199)])
    unposted = await store.filter_unposted(ids)
    assert unposted == [m for n, m in enumerate(ids) if n not in (0, 499, 500, 501, 1199)]


async def test_mark_posted_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.mark_posted(["a", "a"])
    await store.mark_posted(["a"])
    assert await store.filter_unposted(["a", "b"]) == ["b"]
    assert _posted_count(tmp_path / "links.sqlite3") == 1


async def test_mark_posted_prunes_to_newest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_module, "MAX_POSTED_MATCHES", 3)
    clock = iter([100.0, 200.0, 300.0])
    monkeypatch.setattr(store_module.time, "time", lambda: next(clock))
    store = _store(tmp_path)
    await store.mark_posted(["a", "b"])
    await store.mark_posted(["c"])
    assert _posted_count(tmp_path / "links.sqlite3") == 3
    await store.mark_posted(["d", "e"])
    assert _posted_count(tmp_path / "links.sqlite3") == 3
    assert await store.filter_unposted(["a", "b", "c", "d", "e"]) == ["a", "b"]


async def test_match_log_survives_new_instance(tmp_path: Path) -> None:
    first = _store(tmp_path)
    await first.mark_seeded(STEAM_A)
    await first.mark_posted(["a"])
    second = _store(tmp_path)
    assert await second.is_seeded(STEAM_A) is True
    assert await second.filter_unposted(["a", "b"]) == ["b"]


async def test_old_database_with_only_links_upgrades(tmp_path: Path) -> None:
    path = tmp_path / "links.sqlite3"
    _create_old_database(path)
    store = SqliteLinkStore(path)
    assert await store.all_links() == {1: STEAM_A}
    assert await store.is_seeded(STEAM_A) is False
    await store.mark_seeded(STEAM_A)
    await store.mark_posted(["a"])
    assert await store.filter_unposted(["a", "b"]) == ["b"]
    assert await store.get(1) == STEAM_A


async def test_match_log_calls_run_in_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real_to_thread = asyncio.to_thread

    async def recorder(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        calls.append(func.__name__)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", recorder)
    store = _store(tmp_path)
    await store.mark_seeded(STEAM_A)
    await store.is_seeded(STEAM_A)
    await store.mark_posted(["a"])
    await store.filter_unposted(["a"])
    assert calls == ["_mark_seeded", "_is_seeded", "_mark_posted", "_posted_ids"]


def _create_old_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        with conn:
            conn.execute(
                "CREATE TABLE links (discord_id INTEGER PRIMARY KEY, steam64_id TEXT NOT NULL)"
            )
            conn.execute("INSERT INTO links VALUES (1, ?)", (STEAM_A,))
    finally:
        conn.close()


def _posted_count(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT COUNT(*) FROM posted_matches").fetchone()
    finally:
        conn.close()
    return int(row[0])
