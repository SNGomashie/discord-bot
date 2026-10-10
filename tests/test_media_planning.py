import pytest

from ninja_bot.media.models import InvalidUrlError
from ninja_bot.media.planning import validate_url


@pytest.mark.parametrize(
    "url",
    ["https://www.youtube.com/watch?v=abc", "http://example.com/video", "HTTPS://Example.com/x"],
)
def test_validate_url_valid(url: str) -> None:
    assert validate_url(url) == url


def test_validate_url_strips_whitespace() -> None:
    assert validate_url("  https://example.com/v \n") == "https://example.com/v"


@pytest.mark.parametrize(
    "text",
    ["ftp://example.com/file", "example.com/video", "", "   ", "https://", "not a url"],
)
def test_validate_url_invalid(text: str) -> None:
    with pytest.raises(InvalidUrlError):
        validate_url(text)
