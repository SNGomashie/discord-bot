import pytest

from ninja_bot.config import ConfigError, load_config


def test_loads_valid_dev_config() -> None:
    cfg = load_config({"ENVIRONMENT": "dev", "DISCORD_TOKEN": "t", "GUILD_ID": "123"})
    assert cfg.is_dev
    assert cfg.guild_id == 123


def test_missing_token_raises() -> None:
    with pytest.raises(ConfigError):
        load_config({"GUILD_ID": "1"})


def test_dev_requires_guild() -> None:
    with pytest.raises(ConfigError):
        load_config({"ENVIRONMENT": "dev", "DISCORD_TOKEN": "t"})


def test_prod_guild_optional() -> None:
    cfg = load_config({"ENVIRONMENT": "prod", "DISCORD_TOKEN": "t"})
    assert cfg.guild_id is None
