from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    environment: str
    token: str
    guild_id: int | None

    @property
    def is_dev(self) -> bool:
        return self.environment == "dev"


def load_config(env: dict[str, str] | None = None) -> Config:
    """Build a Config from environment variables (or the given mapping)."""
    source = os.environ if env is None else env
    environment = source.get("ENVIRONMENT", "dev")
    token = source.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise ConfigError("DISCORD_TOKEN is not set")
    raw_guild = source.get("GUILD_ID", "").strip()
    guild_id = int(raw_guild) if raw_guild else None
    if environment == "dev" and guild_id is None:
        raise ConfigError("GUILD_ID is required when ENVIRONMENT=dev")
    return Config(environment=environment, token=token, guild_id=guild_id)
