from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ninja_bot.cs2.store import LinkStore, SqliteLinkStore

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
    await store.remove(1)
    assert calls == ["_set", "_get", "_all_links", "_remove"]
