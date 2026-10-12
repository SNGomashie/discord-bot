from __future__ import annotations

import os
import zoneinfo
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
    activity_channel_id: int | None = None
    activity_timezone: str = "UTC"
    toasty_bot_id: int | None = None
    sessions_timezone: str = "UTC"

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
    # Automatic CS2 match posts; unset channel = off.
    cs2_channel_id = _parse_optional_positive_int(source, "CS2_CHANNEL_ID")
    cs2_poll_seconds = _parse_positive_int(source, "CS2_POLL_SECONDS", 300)
    data_dir = source.get("DATA_DIR", "").strip() or "data"
    # Monthly activity report channel; unset = no auto-post.
    activity_channel_id = _parse_optional_positive_int(source, "ACTIVITY_CHANNEL_ID")
    activity_timezone = _parse_timezone(source, "ACTIVITY_TIMEZONE", "UTC")
    # Session poll slots; unset = same zone as activity.
    sessions_timezone = _parse_timezone(source, "SESSIONS_TIMEZONE", activity_timezone)
    # Toasty ;pokemon tracking; set = Message Content intent requested.
    toasty_bot_id = _parse_optional_positive_int(source, "TOASTY_BOT_ID")
    return Config(
        environment=environment,
        token=token,
        guild_id=guild_id,
        max_duration_seconds=max_duration_seconds,
        leetify_api_key=leetify_api_key,
        cs2_channel_id=cs2_channel_id,
        cs2_poll_seconds=cs2_poll_seconds,
        data_dir=data_dir,
        activity_channel_id=activity_channel_id,
        activity_timezone=activity_timezone,
        toasty_bot_id=toasty_bot_id,
        sessions_timezone=sessions_timezone,
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


def _parse_timezone(source: Mapping[str, str], key: str, default: str) -> str:
    raw = source.get(key, "").strip()
    if not raw:
        return default
    try:
        zoneinfo.ZoneInfo(raw)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"{key} must be an IANA time zone name, got {raw!r}") from None
    return raw
