import pytest

from ninja_bot.media.clip import (
    MAX_TIMESTAMP_LENGTH,
    Clip,
    format_timestamp,
    parse_clip,
    parse_timestamp,
    resolve_clip,
)
from ninja_bot.media.models import InvalidClipError, MediaError


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("90", 90.0),
        ("90.5", 90.5),
        ("0", 0.0),
        ("  45  ", 45.0),
        ("1:30", 90.0),
        ("0:05", 5.0),
        ("1:5", 65.0),
        ("90:00", 5400.0),
        ("1:30.5", 90.5),
        ("1:02:03", 3723.0),
        ("1:02:03.5", 3723.5),
        ("10:00:00", 36000.0),
        ("1h2m3s", 3723.0),
        ("1H2M3S", 3723.0),
        ("2m", 120.0),
        ("45s", 45.0),
        ("1h", 3600.0),
        ("1h30s", 3630.0),
        ("1m30.5s", 90.5),
        (" 1M30S ", 90.0),
    ],
)
def test_parse_timestamp_accepts(text: str, expected: float) -> None:
    assert parse_timestamp(text) == pytest.approx(expected)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "-5",
        "-1:30",
        "abc",
        "1.2.3",
        "nan",
        "inf",
        "30s1m",
        "1m1h",
        "1m1m",
        "1s1s",
        "1h1h",
        "1:75",
        "1:60",
        "1:60:00",
        "1:00:60",
        "1:2:3:4",
        "1:",
        ":30",
        "1m 30s",
        "1.5m",
        "h",
        "1:30s",
    ],
)
def test_parse_timestamp_rejects(text: str) -> None:
    with pytest.raises(InvalidClipError):
        parse_timestamp(text)


@pytest.mark.parametrize(
    "text",
    [
        "9" * 5000,
        "9" * 5000 + "m",
        "9" * 5000 + "h",
        "1:" + "9" * 5000 + ":00",
        "0." + "9" * 5000,
        "9" * (MAX_TIMESTAMP_LENGTH + 1),
        "1e400",
        "1e5",
        "inf",
        "-inf",
        "infinity",
        "nan",
        "NaN",
        "\u0663\u0660",  # Arabic-Indic digits "30"
        "\uff19\uff10",  # fullwidth digits "90"
        "1:\u0663\u0660",
        "\u0661m",
        "\u00b2",  # superscript two
        "1_000",
        "0x10",
        "\x00",
    ],
)
def test_parse_timestamp_odd_input_only_raises_invalid_clip(text: str) -> None:
    with pytest.raises(InvalidClipError):
        parse_timestamp(text)


def test_parse_timestamp_length_cap_applies_after_trim() -> None:
    text = "1" * MAX_TIMESTAMP_LENGTH
    assert parse_timestamp(f"  {text}  ") == float(text)
    with pytest.raises(InvalidClipError):
        parse_timestamp(text + "1")


def test_parse_clip_huge_input_is_invalid_clip() -> None:
    with pytest.raises(InvalidClipError):
        parse_clip("9" * 5000, None)
    with pytest.raises(InvalidClipError):
        parse_clip("0", "9" * 5000 + "m")


def test_invalid_clip_error_is_media_error_with_examples() -> None:
    error = InvalidClipError()
    assert isinstance(error, MediaError)
    assert "1:30" in error.message
    assert "1m30s" in error.message


@pytest.mark.parametrize(("start", "end"), [(None, None), ("", None), (None, ""), ("  ", "  ")])
def test_parse_clip_none_when_blank(start: str | None, end: str | None) -> None:
    assert parse_clip(start, end) is None


def test_parse_clip_start_only() -> None:
    assert parse_clip("1:30", None) == Clip(90.0, None)
    assert parse_clip("1:30", " ") == Clip(90.0, None)


def test_parse_clip_start_and_end() -> None:
    assert parse_clip("1m", "2:45.5") == Clip(60.0, 165.5)


def test_parse_clip_zero_start() -> None:
    assert parse_clip("0", "10") == Clip(0.0, 10.0)


def test_parse_clip_end_only_rejected() -> None:
    with pytest.raises(InvalidClipError, match="start time too"):
        parse_clip(None, "1:00")
    with pytest.raises(InvalidClipError, match="start time too"):
        parse_clip("  ", "1:00")


@pytest.mark.parametrize(("start", "end"), [("1:00", "1:00"), ("2:00", "1:00")])
def test_parse_clip_end_not_after_start(start: str, end: str) -> None:
    with pytest.raises(InvalidClipError, match="after the start"):
        parse_clip(start, end)


def test_parse_clip_bad_timestamp() -> None:
    with pytest.raises(InvalidClipError):
        parse_clip("soon", None)
    with pytest.raises(InvalidClipError):
        parse_clip("1:00", "later")


def test_clip_is_frozen() -> None:
    clip = Clip(1.0, 2.0)
    with pytest.raises(AttributeError):
        clip.start_s = 3.0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0:00"),
        (5, "0:05"),
        (90, "1:30"),
        (90.0, "1:30"),
        (90.5, "1:30.5"),
        (599, "9:59"),
        (3600, "1:00:00"),
        (3725, "1:02:05"),
        (3725.5, "1:02:05.5"),
        (59.96, "1:00"),
    ],
)
def test_format_timestamp(seconds: float, expected: str) -> None:
    assert format_timestamp(seconds) == expected


@pytest.mark.parametrize(
    ("clip", "expected"),
    [
        (Clip(10, None), Clip(10, 600)),
        (Clip(10, 20.5), Clip(10, 20.5)),
        (Clip(10, 900), Clip(10, 600)),
        (Clip(0, 600), Clip(0, 600)),
        (Clip(599, None), Clip(599, 600)),
    ],
)
def test_resolve_clip(clip: Clip, expected: Clip) -> None:
    assert resolve_clip(clip, 600) == expected


@pytest.mark.parametrize("start", [600, 601, 5000])
def test_resolve_clip_start_past_end(start: float) -> None:
    with pytest.raises(InvalidClipError, match="10:00"):
        resolve_clip(Clip(start, None), 600)


@pytest.mark.parametrize("clip", [Clip(599.5, None), Clip(10, 10.5), Clip(599.5, 900)])
def test_resolve_clip_shorter_than_one_second(clip: Clip) -> None:
    with pytest.raises(InvalidClipError, match="1 second"):
        resolve_clip(clip, 600)


@pytest.mark.parametrize(
    ("clip", "expected"),
    [
        (Clip(50, None), Clip(50, 100.9)),
        (Clip(99.9, None), Clip(99.9, 100.9)),
        (Clip(99, 200), Clip(99, 100.9)),
    ],
)
def test_resolve_clip_float_duration(clip: Clip, expected: Clip) -> None:
    assert resolve_clip(clip, 100.9) == expected


@pytest.mark.parametrize("start", [100, 100.2, 100.5])
def test_resolve_clip_float_duration_too_short(start: float) -> None:
    # Under a second is left, but the start is not past the end of the video.
    with pytest.raises(InvalidClipError, match="1 second"):
        resolve_clip(Clip(start, None), 100.9)


def test_resolve_clip_float_duration_start_past_end_shows_whole_seconds() -> None:
    with pytest.raises(InvalidClipError, match=r"\(1:40 long\)"):
        resolve_clip(Clip(100.9, None), 100.9)
