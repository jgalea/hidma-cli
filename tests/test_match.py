import pytest

from hidma.match import MatchError, match_one

ITEMS = [
    {"id": "a1", "name": "Website rebuild", "code": "WEB"},
    {"id": "a2", "name": "Website maintenance", "code": "WEBM"},
    {"id": "a3", "name": "Mobile app", "code": "APP"},
]


def test_substring_unique():
    assert match_one(ITEMS, "mobile", "project")["id"] == "a3"


def test_case_insensitive_and_id():
    assert match_one(ITEMS, "MOBILE APP", "project")["id"] == "a3"
    assert match_one(ITEMS, "a2", "project")["id"] == "a2"


def test_exact_name_beats_substring():
    assert match_one(ITEMS, "website rebuild", "project")["id"] == "a1"


def test_ambiguous():
    with pytest.raises(MatchError, match="ambiguous"):
        match_one(ITEMS, "website", "project")


def test_none():
    with pytest.raises(MatchError, match="no project matches"):
        match_one(ITEMS, "nothing", "project")


def test_extra_name_fields():
    assert match_one(ITEMS, "webm", "project", names=("name", "code"))["id"] == "a2"
