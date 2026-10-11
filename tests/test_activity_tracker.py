import pytest

from ninja_bot.activity.tracker import Action, Close, Move, Nothing, Open, plan_transition

AFK = 99


@pytest.mark.parametrize(
    ("before", "after", "afk", "expected"),
    [
        pytest.param(None, 1, AFK, Open(1), id="join"),
        pytest.param(1, None, AFK, Close(), id="leave"),
        pytest.param(1, 2, AFK, Move(2), id="move"),
        pytest.param(1, AFK, AFK, Close(), id="join-afk"),
        pytest.param(None, AFK, AFK, Nothing(), id="connect-straight-to-afk"),
        pytest.param(AFK, 1, AFK, Open(1), id="leave-afk"),
        pytest.param(AFK, None, AFK, Nothing(), id="disconnect-from-afk"),
        pytest.param(1, 1, AFK, Nothing(), id="mute-deafen-stream"),
        pytest.param(None, None, AFK, Nothing(), id="no-channel"),
        pytest.param(None, 1, None, Open(1), id="join-without-afk-channel"),
        pytest.param(1, None, None, Close(), id="leave-without-afk-channel"),
    ],
)
def test_plan_transition(
    before: int | None, after: int | None, afk: int | None, expected: Action
) -> None:
    assert plan_transition(before, after, afk) == expected
