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

    def job_shortcode(self, job):
        return job["type"]["shortcode"]

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


def entry(day, minutes, project="Acme website", job="Consulting", client="Acme", cell_id="cell-1"):
    return {"id": cell_id, "date": day, "client": client, "project": project, "project_id": "p-1", "job": job, "job_id": "j-1",
            "minutes": minutes, "not_billable": 0, "comments": "", "timesheet_id": "ts-1", "row_id": "row-1", "cell": {"id": cell_id}}


class ReadingHidma(FakeHidma):
    def entries(self, start, end):
        self.calls.append(("entries", start, end))
        return [entry("2026-10-05", 60), entry("2026-10-06", 30, cell_id="cell-2")]

    def recent_entries(self, count, weeks_back=12):
        return [entry("2026-10-01", 45, cell_id="cell-0")][:count]

    def week_timesheet(self, day):
        return {"id": "ts-1", "status": "st-open", "rows": [{"id": "row-1", "job": {"id": "j-1", "name": "Consulting"},
                 "project": {"id": "p-1", "name": "Acme website", "client": {"name": "Acme"}},
                 "cells": [{"id": "cell-1", "date": "2026-10-06T00:00:00.000Z", "minutes": 90}]}]}

    def timesheet_state_name(self, ts):
        return "open"

    def raw_report(self, report_type="unbilled", start=None, end=None, client_ids=None, project_ids=None):
        self.calls.append(("raw_report", report_type, client_ids))
        return {"rows": [{"project": {"name": "Acme website", "client": {"name": "Acme"}}, "job": {"name": "Consulting"},
                          "minutes": 695, "cost": 115833}], "totals": {"totalMinutes": 695, "totalCost": 115833}}


@pytest.fixture
def reading(monkeypatch):
    f = ReadingHidma()
    monkeypatch.setattr(api_mod, "Hidma", lambda *a, **k: f)
    return f


def test_today_uses_todays_range(reading, capsys):
    assert cli.main(["today"]) == 0
    assert reading.calls == [("entries", date.today(), date.today())]
    assert "total:       1h30" in capsys.readouterr().out


def test_last_lists_recent(reading, capsys):
    assert cli.main(["last", "1"]) == 0
    out = capsys.readouterr().out
    assert "2026-10-01" in out and "45m" in out and "cell-0" in out


def test_week_prints_grid(reading, capsys):
    assert cli.main(["week", "--date", "2026-10-07"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("week of 2026-10-05 (2026-10-05 to 2026-10-11)  [open]")
    assert "Acme / Acme website / Consulting" in out and "1h30" in out


def test_report_groups_and_month_range(reading, capsys):
    assert cli.main(["report", "--by", "day", "--month", "2026-09"]) == 0
    assert reading.calls[-1] == ("entries", date(2026, 9, 1), date(2026, 9, 30))
    out = capsys.readouterr().out
    assert out.splitlines()[0].split() == ["DAY", "TIME", "BILLABLE", "NOT", "BILLABLE", "ENTRIES"]
    assert "2026-10-05" in out and "total:       1h30" in out


def test_report_csv(reading, capsys):
    assert cli.main(["report", "--by", "client", "--csv"]) == 0
    assert capsys.readouterr().out.splitlines() == ["key,minutes,billable,not_billable,entries", "Acme,90,90,0,2"]


def test_report_unbilled(reading, capsys):
    assert cli.main(["report", "--unbilled", "--client", "acme"]) == 0
    assert reading.calls[-1] == ("raw_report", "unbilled", ["c-1"])
    out = capsys.readouterr().out
    assert "11h35" in out and "1,158.33" in out and "total:       11h35  cost 1,158.33" in out


def test_bad_month_exits_2(reading, capsys):
    with pytest.raises(SystemExit):
        cli.main(["report", "--month", "2026-13"])


class TimerHidma(FakeHidma):
    def __init__(self):
        super().__init__()
        self.timer = None

    def active_timer(self, day=None):
        return self.timer

    def start_timer(self, **kw):
        self.calls.append(("start", kw))
        self.timer = {"id": "tm-1", "timesheet_id": "ts-1", "timesheetRowCellId": "cell-1",
                      "timerEvents": [{"eventType": "START", "startTime": "2026-10-07T09:00:00"}], "comments": kw["comments"]}
        return {"timer": self.timer, "timesheet_id": "ts-1", "row_id": "row-1", "cell_id": "cell-1", "base_minutes": 30}

    def timer_event(self, timer, kind, now=None):
        self.calls.append(("event", kind))
        timer["timerEvents"].append({"eventType": kind, "startTime": "2026-10-07T09:30:00"})

    def stop_timer(self, timer=None):
        self.calls.append(("stop", timer["id"]))
        return {"timer_id": "tm-1", "seconds": 1810, "base_minutes": 30, "expected_minutes": 60, "cli_wrote_minutes": False,
                "cell": {k: v for k, v in entry("2026-10-07", 60).items() if k != "cell"}}

    def discard_timer(self, timer=None):
        self.calls.append(("discard", timer["id"]))
        return {"timer_id": "tm-1", "gone": True, "cell_id": "cell-1", "cell_minutes": 30, "cell_present": True}

    def timesheet(self, ts_id):
        return ReadingHidma().week_timesheet(None)


@pytest.fixture
def timers(monkeypatch):
    f = TimerHidma()
    monkeypatch.setattr(api_mod, "Hidma", lambda *a, **k: f)
    return f


def test_timer_start_resolves_target(timers, capsys):
    assert cli.main(["timer", "start", "ZZTEST", "--project", "site", "--job", "consulting", "--no-billable"]) == 0
    kind, kw = timers.calls[0]
    assert kw["project"]["id"] == "p-1" and kw["job"]["id"] == "j-1" and kw["comments"] == "ZZTEST" and kw["billable"] is False
    out = capsys.readouterr().out
    assert "timer started on Acme / Acme website / Consulting (today's entry already holds 30m)" in out


def test_timer_status_pause_resume_stop(timers, capsys):
    assert cli.main(["timer", "status"]) == 0
    assert "no running or paused timer" in capsys.readouterr().out
    assert cli.main(["timer", "stop"]) == 2
    cli.main(["timer", "start", "x", "--project", "site", "--job", "consulting"])
    capsys.readouterr()
    assert cli.main(["timer", "status"]) == 0
    status = capsys.readouterr().out
    assert status.startswith("start ") and "on Acme / Acme website / Consulting since 2026-10-07 09:00" in status
    assert cli.main(["timer", "resume"]) == 2
    assert cli.main(["timer", "pause"]) == 0
    assert cli.main(["timer", "pause"]) == 2
    assert cli.main(["timer", "resume"]) == 0
    assert cli.main(["timer", "stop"]) == 0
    out = capsys.readouterr().out
    assert "stopped after 0h30m10s" in out and "Acme / Acme website / Consulting on 2026-10-07: now 1h (id cell-1)" in out
    assert [c for c in timers.calls if c[0] in ("event", "stop")] == [("event", "PAUSE"), ("event", "RESUME"), ("stop", "tm-1")]


def test_timer_discard_needs_yes(timers, capsys):
    cli.main(["timer", "start", "x", "--project", "site", "--job", "consulting"])
    assert cli.main(["timer", "discard"]) == 2
    assert "--yes" in capsys.readouterr().err
    assert cli.main(["timer", "discard", "--yes"]) == 0
    assert "timer discarded; its entry cell-1 is still there with 30m" in capsys.readouterr().out
    assert timers.calls[-1] == ("discard", "tm-1")


def test_timer_list_shows_target_and_elapsed(timers, capsys):
    cli.main(["timer", "start", "x", "--project", "site", "--job", "consulting"])
    timers.timers_for_week = lambda day: {"timesheet": {"id": "ts-1"}, "timers": [timers.timer]}
    timers.timer["timerEvents"].append({"eventType": "PAUSE", "startTime": "2026-10-07T10:05:00"})
    capsys.readouterr()
    assert cli.main(["timers"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].split() == ["STATE", "ELAPSED", "STARTED", "ON", "COMMENT", "ID"]
    assert "pause" in out and "1h05" in out and "Acme / Acme website / Consulting" in out and "2026-10-07 09:00" in out
