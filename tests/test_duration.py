import pytest

from hidma.duration import format_minutes, parse_minutes


@pytest.mark.parametrize("text,minutes", [
    ("1h30", 90), ("1h 30m", 90), ("1H30M", 90), ("90m", 90), ("90", 90), ("1.5h", 90), ("1:30", 90),
    ("2h", 120), ("45m", 45), ("45min", 45), ("0.25h", 15), ("1h05", 65), ("0:05", 5),
])
def test_parse(text, minutes):
    assert parse_minutes(text) == minutes


@pytest.mark.parametrize("text", ["", "0", "0m", "abc", "1x", "1:60", "-5m", "h"])
def test_parse_rejects(text):
    with pytest.raises(ValueError):
        parse_minutes(text)


def test_format():
    assert format_minutes(90) == "1h30"
    assert format_minutes(60) == "1h"
    assert format_minutes(45) == "45m"
    assert format_minutes(0) == "0m"
    assert format_minutes(65) == "1h05"
