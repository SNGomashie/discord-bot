from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    environment: str
    token: str
    guild_id: int | None
    max_duration_seconds: int = 3600
    leetify_api_key: str | None = None
    cs2_channel_id: int | None = None
    cs2_poll_seconds: int = 300
    data_dir: str = "data"

    @property
    def is_dev(self) -> bool:
        return self.environment == "dev"


def load_config(env: dict[str, str] | None = None) -> Config:
    """Build a Config from environment variables (or the given mapping)."""
    source: Mapping[str, str] = os.environ if env is None else env
    environment = source.get("ENVIRONMENT", "dev")
    token = source.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise ConfigError("DISCORD_TOKEN is not set")
    raw_guild = source.get("GUILD_ID", "").strip()
    guild_id = int(raw_guild) if raw_guild else None
    if environment == "dev" and guild_id is None:
        raise ConfigError("GUILD_ID is required when ENVIRONMENT=dev")
    max_duration_seconds = _parse_positive_int(source, "MAX_DURATION_SECONDS", 3600)
    leetify_api_key = source.get("LEETIFY_API_KEY", "").strip() or None
    cs2_channel_id = _parse_optional_positive_int(source, "CS2_CHANNEL_ID")
    cs2_poll_seconds = _parse_positive_int(source, "CS2_POLL_SECONDS", 300)
    data_dir = source.get("DATA_DIR", "").strip() or "data"
    return Config(
        environment=environment,
        token=token,
        guild_id=guild_id,
        max_duration_seconds=max_duration_seconds,
        leetify_api_key=leetify_api_key,
        cs2_channel_id=cs2_channel_id,
        cs2_poll_seconds=cs2_poll_seconds,
        data_dir=data_dir,
    )


def _parse_positive_int(source: Mapping[str, str], key: str, default: int) -> int:
    value = _parse_optional_positive_int(source, key)
    return default if value is None else value


def _parse_optional_positive_int(source: Mapping[str, str], key: str) -> int | None:
    raw = source.get(key, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"{key} must be positive, got {value}")
    return value
