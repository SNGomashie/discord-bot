from ninja_bot.cogs.core import format_latency


def test_format_latency() -> None:
    assert format_latency(0.0421) == "Pong! 42 ms"
