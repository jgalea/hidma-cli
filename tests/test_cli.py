from datetime import date

import pytest

from hidma import api as api_mod
from hidma import cli
from hidma import session as session_mod
from hidma.client import HidmaError, SessionExpired
from hidma.match import MatchError


class FakeHidma:
    def __init__(self):
        self.calls = []
        self.user_id = "user-1"

    def clients(self, term=None):
        return [{"id": "c-1", "name": "Acme"}]

    def projects(self, client_id=None, term=None):
        return [{"id": "p-1", "name": "Acme website", "code": "ACM", "client": {"name": "Acme"}},
                {"id": "p-2", "name": "Acme ads", "code": "ADS", "client": {"name": "Acme"}}]

    def jobs(self, term=None):
        return [{"id": "j-1", "name": "Consulting", "type": {"shortcode": "b"}, "active": True},
                {"id": "j-2", "name": "Admin", "type": {"shortcode": "i"}, "active": True}]

    def log(self, **kw):
        self.calls.append(kw)
        return {"id": "cell-1", "date": str(kw["day"]), "client": "Acme", "project": "Acme website", "job": "Consulting",
                "minutes": kw["minutes"], "not_billable": 0 if kw["billable"] else kw["minutes"], "comments": kw["comments"],
                "action": "new-row", "timesheet_id": "ts", "row_id": "r", "cell": {}}


@pytest.fixture
def fake(monkeypatch):
    f = FakeHidma()
    monkeypatch.setattr(api_mod, "Hidma", lambda *a, **k: f)
    return f


def test_log_resolves_project_and_default_job(fake, capsys):
    assert cli.main(["log", "1h30", "planning", "--project", "site", "--date", "2026-10-07"]) == 0
    call = fake.calls[0]
    assert call["project"]["id"] == "p-1" and call["job"]["id"] == "j-1" and call["minutes"] == 90
    assert call["day"] == date(2026, 10, 7) and call["billable"] is True
    assert "logged 1h30 on 2026-10-07 for Acme / Acme website / Consulting" in capsys.readouterr().out


def test_log_ambiguous_project_exits_2(fake, capsys):
    assert cli.main(["log", "1h", "x", "--project", "acme"]) == 2
    assert "ambiguous" in capsys.readouterr().err
    assert fake.calls == []


def test_log_bad_duration_exits_2(fake, capsys):
    assert cli.main(["log", "abc", "x", "--project", "site"]) == 2
    assert "duration" in capsys.readouterr().err


def test_log_business_job_needs_project(fake, capsys):
    assert cli.main(["log", "1h", "x", "--job", "consulting"]) == 2
    assert "needs a project" in capsys.readouterr().err


def test_log_internal_job_without_project(fake):
    assert cli.main(["log", "30m", "x", "--job", "admin", "--no-billable", "--json"]) == 0
    assert fake.calls[0]["project"] is None and fake.calls[0]["billable"] is False


def test_delete_without_yes_exits_2(monkeypatch, capsys):
    class H(FakeHidma):
        def find_entry(self, cell_id, hint=None, weeks_back=12):
            return {"id": cell_id, "date": "2026-10-07", "client": "", "project": "P", "job": "J", "minutes": 30,
                    "not_billable": 0, "comments": "", "timesheet_id": "t", "row_id": "r", "cell": {"id": cell_id}}

        def delete_cell(self, entry):
            raise AssertionError("must not delete without --yes")

    monkeypatch.setattr(api_mod, "Hidma", lambda *a, **k: H())
    assert cli.main(["delete", "cell-1"]) == 2
    assert "--yes" in capsys.readouterr().err


def test_session_errors_exit_4(monkeypatch, capsys):
    def boom(*a, **k):
        raise SessionExpired("session expired, run `hidma login`")
    monkeypatch.setattr(api_mod, "Hidma", boom)
    assert cli.main(["clients"]) == 4
    assert "hidma login" in capsys.readouterr().err

    def no_session(*a, **k):
        raise session_mod.NotLoggedIn("no saved session, run `hidma login`")
    monkeypatch.setattr(api_mod, "Hidma", no_session)
    assert cli.main(["projects"]) == 4


def test_api_errors_exit_1(monkeypatch, capsys):
    def boom(*a, **k):
        raise HidmaError("HTTP 500 GET clients/: nope", status=500)
    monkeypatch.setattr(api_mod, "Hidma", boom)
    assert cli.main(["clients"]) == 1
    assert "HTTP 500" in capsys.readouterr().err
