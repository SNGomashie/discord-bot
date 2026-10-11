import pytest

from ninja_bot.cs2 import formatting
from ninja_bot.cs2.embed import FIELD_VALUE_LIMIT
from ninja_bot.cs2.formatting import (
    BAR_EMPTY,
    BAR_FILLED,
    BAR_WIDTH,
    ESCAPED_NAME_LENGTH,
    NO_PLAYERS,
    NO_RECENT_MATCHES,
    STAR,
    clean_name,
    display_width,
    escape_name,
    form_strip,
    format_rating,
    format_team_table,
    rating_bar,
)
from ninja_bot.cs2.models import MatchOutcome, PlayerMatchStats, RecentMatch


def _player(index: int, **kwargs: object) -> PlayerMatchStats:
    defaults: dict[str, object] = {
        "name": f"player{index}",
        "kills": 20 - index,
        "deaths": 15,
        "assists": 4,
        "hs_kills": 10,
        "damage": 2000,
        "rounds": 24,
        "mvps": index,
        "leetify_rating": 0.05 - index / 100,
    }
    defaults.update(kwargs)
    return PlayerMatchStats(steam64_id=f"7656119{index:010d}", **defaults)  # type: ignore[arg-type]


def _rows(table: str) -> list[str]:
    assert table.startswith("```\n")
    assert table.endswith("\n```")
    return table.removeprefix("```\n").removesuffix("\n```").split("\n")


def _recent(outcome: MatchOutcome | None, rating: float | None = None) -> RecentMatch:
    return RecentMatch(
        id="m",
        finished_at=None,
        map_name="de_mirage",
        outcome=outcome,
        leetify_rating=rating,
        score=None,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.05, "+0.05"),
        (-0.03, "-0.03"),
        (0.0, "+0.00"),
        (-0.001, "+0.00"),
        (0.126, "+0.13"),
        (1.5, "+1.50"),
        (None, "-"),
    ],
)
def test_format_rating(value: float | None, expected: str) -> None:
    assert format_rating(value) == expected


@pytest.mark.parametrize("count", [5, 10])
def test_team_table_has_header_and_one_row_per_player(count: int) -> None:
    players = [_player(i) for i in range(count)]
    rows = _rows(format_team_table(players))

    assert len(rows) == count + 1
    assert "K/D/A" in rows[0]
    assert "ADR" in rows[0]
    assert "HS%" in rows[0]
    assert "Rating" in rows[0]
    for i, row in enumerate(rows[1:]):
        assert f"player{i}" in row
    assert len({len(row) for row in rows}) == 1


def test_team_table_row_values() -> None:
    row = _rows(format_team_table([_player(0, kills=20, deaths=15, assists=4)]))[1]

    assert "20/15/4" in row
    assert " 83 " in row  # 2000 damage / 24 rounds
    assert " 50 " in row  # 10 of 20 kills
    assert row.endswith("+0.05")


def test_star_goes_to_most_mvps() -> None:
    players = [_player(0, mvps=1), _player(1, mvps=6), _player(2, mvps=3)]
    rows = _rows(format_team_table(players))[1:]

    assert [row.startswith(STAR) for row in rows] == [False, True, False]
    assert "".join(rows).count(STAR) == 1


def test_star_ties_broken_by_rating() -> None:
    players = [_player(0, mvps=3, leetify_rating=0.01), _player(1, mvps=3, leetify_rating=0.09)]
    rows = _rows(format_team_table(players))[1:]

    assert rows[1].startswith(STAR)
    assert not rows[0].startswith(STAR)


def test_star_falls_back_to_rating_without_mvps() -> None:
    players = [
        _player(0, mvps=None, leetify_rating=-0.02),
        _player(1, mvps=None, leetify_rating=0.04),
    ]
    rows = _rows(format_team_table(players))[1:]

    assert rows[1].startswith(STAR)


def test_no_star_without_mvps_or_ratings() -> None:
    players = [
        PlayerMatchStats(steam64_id="1", name="a"),
        PlayerMatchStats(steam64_id="2", name="b"),
    ]

    assert STAR not in format_team_table(players)


def test_missing_values_render_as_dash() -> None:
    row = _rows(format_team_table([PlayerMatchStats(steam64_id="1", name="ghost")]))[1]

    assert row.split() == ["ghost", "-/-/-", "-", "-", "-"]


def test_partial_kda() -> None:
    row = _rows(
        format_team_table([PlayerMatchStats(steam64_id="1", name="x", kills=3, assists=1)])
    )[1]

    assert "3/-/1" in row


def test_empty_team() -> None:
    rows = _rows(format_team_table([]))

    assert rows[1] == NO_PLAYERS


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("short", "short"),
        ("exactly12chr", "exactly12chr"),
        ("a much longer player name", "a much long…"),
        ("twentycharacterslong", "twentychara…"),
        ("```evil```", "evil"),
        ("line\nbreak\r\n", "linebreak"),
        ("tab\there", "tabhere"),
        ("", "?"),
        ("```", "?"),
        (None, "?"),
        ("  padded  ", "padded"),
    ],
)
def test_clean_name(name: str | None, expected: str) -> None:
    assert clean_name(name) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Ninja", "Ninja"),
        ("[free nitro](https://evil.example)", r"\[free nitro\]\(https://evil.examp…"),
        ("[x](https://a.b)", r"\[x\]\(https://a.b\)"),
        ("**bold**", r"\*\*bold\*\*"),
        ("_under_", r"\_under\_"),
        ("~~strike~~", r"\~\~strike\~\~"),
        ("||spoiler||", r"\|\|spoiler\|\|"),
        ("`code`", r"\`code\`"),
        # A leading backslash must not cancel the escape of the link bracket.
        (r"\[x](https://a.b)", r"\\\[x\]\(https://a.b\)"),
        ("line\nbreak\r\n", "linebreak"),
        ("bell\x07\x1bname\u200b", "bellname"),
        ("  padded  ", "padded"),
        ("", "?"),
        ("\n\t", "?"),
        (None, "?"),
    ],
)
def test_escape_name(name: str | None, expected: str) -> None:
    assert escape_name(name) == expected


@pytest.mark.parametrize(
    ("text", "width"),
    [
        ("abc", 3),
        ("", 0),
        ("日本語", 6),
        ("ＡＢ", 4),
        ("gg😀", 4),
        ("e\u0301", 1),
        ("a\u200bb", 2),
    ],
)
def test_display_width(text: str, width: int) -> None:
    assert display_width(text) == width


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("日本語の名前", "日本語の名前"),
        ("日本語の名前です", "日本語の名…"),
        ("ab😀😀😀😀😀", "ab😀😀😀😀😀"),
        ("abc😀😀😀😀😀", "abc😀😀😀😀…"),
        ("e\u0301" * 12, "e\u0301" * 12),
        ("e\u0301" * 13, "e\u0301" * 11 + "…"),
    ],
)
def test_clean_name_truncates_by_display_width(name: str, expected: str) -> None:
    cleaned = clean_name(name)

    assert cleaned == expected
    assert display_width(cleaned) <= formatting.NAME_WIDTH


def test_clean_name_wide_char_that_does_not_fit_is_dropped() -> None:
    # 10 columns of ASCII, then a wide char that would end at column 12.
    cleaned = clean_name("a" * 10 + "日本")

    assert cleaned == "a" * 10 + "…"
    assert display_width(cleaned) == 11


_ODD_NAMES = [
    "Ninja",
    "a much longer player name",
    "日本語の名前です",
    "ab😀😀😀😀😀",
    "😀 gg wp 😀",
    "e\u0301" * 20,
    "",
]


@pytest.mark.parametrize("name_width", [8, 12, 16])
def test_table_rows_have_equal_display_width(
    monkeypatch: pytest.MonkeyPatch, name_width: int
) -> None:
    monkeypatch.setattr(formatting, "NAME_WIDTH", name_width)
    players = [_player(i, name=name) for i, name in enumerate(_ODD_NAMES)]
    rows = _rows(format_team_table(players))

    assert len(rows) == 1 + len(players)
    assert {display_width(row) for row in rows} == {display_width(rows[0])}
    # Name column ends at the same column in every row (after the star column).
    assert all(display_width(clean_name(name)) <= name_width for name in _ODD_NAMES)
    assert rows[0][1 : 1 + name_width] == "Player".ljust(name_width)


def test_widest_names_fit_the_field_limit() -> None:
    players = [_player(i, name="日" * 12) for i in range(10)]
    table = format_team_table(players, max_chars=FIELD_VALUE_LIMIT)
    rows = _rows(table)

    assert len(table) <= FIELD_VALUE_LIMIT
    assert len(rows) == 11
    assert not rows[-1].startswith("+")


def test_escape_name_truncates_long_names() -> None:
    escaped = escape_name("x" * 100)
    assert escaped == "x" * (ESCAPED_NAME_LENGTH - 1) + "…"
    assert len(escaped) == ESCAPED_NAME_LENGTH


def test_escape_name_truncates_before_escaping() -> None:
    escaped = escape_name("*" * 100)
    assert escaped == "\\*" * (ESCAPED_NAME_LENGTH - 1) + "…"


def test_odd_names_cannot_break_the_code_block() -> None:
    players = [_player(0, name="```\n@everyone\n```"), _player(1, name="x" * 40)]
    table = format_team_table(players)
    rows = _rows(table)

    assert table.count("```") == 2
    assert len(rows) == 3
    assert "@everyone" in rows[1]
    assert "xxxxxxxxxxx…" in rows[2]


def test_length_cap_drops_rows_and_adds_more_line() -> None:
    players = [_player(i, name="z" * 32) for i in range(10)]
    full = format_team_table(players)
    limit = len(full) - 1
    table = format_team_table(players, max_chars=limit)
    rows = _rows(table)

    assert len(table) <= limit
    # The "+N more" line is shorter than a row, so dropping one row is enough.
    assert rows[-1] == "+1 more"
    assert len(rows) == 1 + 9 + 1


@pytest.mark.parametrize("max_chars", [60, 100, 200, 300, 500, 1000])
def test_length_cap_is_always_respected(max_chars: int) -> None:
    players = [_player(i, name="z" * 32) for i in range(10)]
    table = format_team_table(players, max_chars=max_chars)

    assert len(table) <= max_chars
    assert table.endswith("\n```")


def test_length_cap_can_drop_every_row() -> None:
    players = [_player(i) for i in range(10)]
    header_only = len(_rows(format_team_table([]))[0]) + len("```\n\n+10 more\n```")
    rows = _rows(format_team_table(players, max_chars=header_only))

    assert rows[1:] == ["+10 more"]


def test_too_small_cap_raises() -> None:
    with pytest.raises(ValueError):
        format_team_table([_player(0)], max_chars=10)


def test_form_strip_empty() -> None:
    assert form_strip([]) == NO_RECENT_MATCHES


def test_form_strip_emoji_and_average() -> None:
    recent = [_recent("win", 0.10), _recent("loss", -0.04), _recent("tie", 0.0), _recent(None)]

    assert form_strip(recent) == "🟩🟥🟨⬜ avg +0.02"


def test_form_strip_uses_newest_five() -> None:
    recent = [_recent("win", 0.05)] * 5 + [_recent("loss", -1.0)] * 3

    assert form_strip(recent) == "🟩🟩🟩🟩🟩 avg +0.05"


def test_form_strip_without_ratings() -> None:
    assert form_strip([_recent("win"), _recent("loss")]) == "🟩🟥"


@pytest.mark.parametrize(
    ("value", "max_value", "filled"),
    [
        (0.0, 100.0, 0),
        (50.0, 100.0, 5),
        (71.4, 100.0, 7),
        (100.0, 100.0, 10),
        (0.12, 0.2, 6),
        (150.0, 100.0, 10),
        (-5.0, 100.0, 0),
        (None, 100.0, 0),
        (float("nan"), 100.0, 0),
        (float("inf"), 100.0, 10),
    ],
)
def test_rating_bar(value: float | None, max_value: float, filled: int) -> None:
    bar = rating_bar(value, max_value)
    assert bar == BAR_FILLED * filled + BAR_EMPTY * (BAR_WIDTH - filled)
    assert len(bar) == BAR_WIDTH


def test_rating_bar_custom_width() -> None:
    assert rating_bar(1.0, 2.0, width=4) == BAR_FILLED * 2 + BAR_EMPTY * 2


@pytest.mark.parametrize(("max_value", "width"), [(0.0, 10), (-1.0, 10), (1.0, 0)])
def test_rating_bar_rejects_bad_bounds(max_value: float, width: int) -> None:
    with pytest.raises(ValueError):
        rating_bar(1.0, max_value, width=width)
