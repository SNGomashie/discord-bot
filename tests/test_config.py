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


def test_max_duration_defaults_to_one_hour() -> None:
    cfg = load_config({"ENVIRONMENT": "prod", "DISCORD_TOKEN": "t"})
    assert cfg.max_duration_seconds == 3600


def test_max_duration_custom_value() -> None:
    cfg = load_config({"ENVIRONMENT": "prod", "DISCORD_TOKEN": "t", "MAX_DURATION_SECONDS": "600"})
    assert cfg.max_duration_seconds == 600


@pytest.mark.parametrize("value", ["abc", "1.5", "0", "-10"])
def test_max_duration_invalid_raises(value: str) -> None:
    with pytest.raises(ConfigError):
        load_config({"ENVIRONMENT": "prod", "DISCORD_TOKEN": "t", "MAX_DURATION_SECONDS": value})


PROD = {"ENVIRONMENT": "prod", "DISCORD_TOKEN": "t"}


def test_cs2_settings_default() -> None:
    cfg = load_config(dict(PROD))
    assert cfg.leetify_api_key is None
    assert cfg.cs2_channel_id is None
    assert cfg.cs2_poll_seconds == 300
    assert cfg.data_dir == "data"


def test_cs2_settings_custom_values() -> None:
    cfg = load_config(
        {
            **PROD,
            "LEETIFY_API_KEY": " key ",
            "CS2_CHANNEL_ID": "987654321",
            "CS2_POLL_SECONDS": "60",
            "DATA_DIR": "/var/lib/ninja",
        }
    )
    assert cfg.leetify_api_key == "key"
    assert cfg.cs2_channel_id == 987654321
    assert cfg.cs2_poll_seconds == 60
    assert cfg.data_dir == "/var/lib/ninja"


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_leetify_key_is_none(value: str) -> None:
    assert load_config({**PROD, "LEETIFY_API_KEY": value}).leetify_api_key is None


def test_empty_data_dir_uses_default() -> None:
    assert load_config({**PROD, "DATA_DIR": ""}).data_dir == "data"


@pytest.mark.parametrize("key", ["CS2_CHANNEL_ID", "CS2_POLL_SECONDS"])
@pytest.mark.parametrize("value", ["abc", "1.5", "0", "-10"])
def test_cs2_ints_invalid_raise(key: str, value: str) -> None:
    with pytest.raises(ConfigError, match=key):
        load_config({**PROD, key: value})
