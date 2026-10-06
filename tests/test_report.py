import io
from datetime import date

import pytest

from hidma import report


def entry(day, minutes, client="Acme", project="Acme website", job="Consulting", nb=0):
    return {"id": f"{day}-{project}-{job}", "date": day, "client": client, "project": project, "job": job,
            "minutes": minutes, "not_billable": nb, "comments": ""}


ENTRIES = [entry("2026-10-05", 60), entry("2026-10-06", 90, nb=30), entry("2026-10-06", 15, client="", project="", job="Admin"),
           entry("2026-10-13", 120, project="Acme ads")]


def test_aggregate_by_client_project_job():
    assert report.aggregate(ENTRIES, "client") == [
        {"key": "Acme", "minutes": 270, "billable": 240, "not_billable": 30, "entries": 3},
        {"key": "(no client)", "minutes": 15, "billable": 15, "not_billable": 0, "entries": 1}]
    assert [r["key"] for r in report.aggregate(ENTRIES, "project")] == ["Acme / Acme website", "Acme / Acme ads", "(no project)"]
    assert [r["key"] for r in report.aggregate(ENTRIES, "job")] == ["Acme / Acme website / Consulting", "Acme / Acme ads / Consulting", "Admin"]


def test_aggregate_by_day_and_week_sorted_by_date():
    assert [(r["key"], r["minutes"]) for r in report.aggregate(ENTRIES, "day")] == [("2026-10-05", 60), ("2026-10-06", 105), ("2026-10-13", 120)]
    assert [(r["key"], r["minutes"]) for r in report.aggregate(ENTRIES, "week")] == [("2026-10-05", 165), ("2026-10-12", 120)]
    with pytest.raises(ValueError, match="--by"):
        report.aggregate(ENTRIES, "colour")


def test_week_grid_layout():
    grid = report.week_grid(ENTRIES[:3], date(2026, 10, 5), "open")
    lines = grid.splitlines()
    assert lines[0] == "week of 2026-10-05 (2026-10-05 to 2026-10-11)  [open]"
    assert lines[1].split() == ["5", "Mon", "6", "Tue", "7", "Wed", "8", "Thu", "9", "Fri", "10", "Sat", "11", "Sun", "TOTAL"]
    assert lines[2].startswith("Acme / Acme website / Consulting") and lines[2].split()[-1] == "2h30"
    assert lines[2].split()[-8:-1] == ["1h", "1h30", "-", "-", "-", "-", "-"]
    assert lines[3].split() == ["Admin", "-", "15m", "-", "-", "-", "-", "-", "15m"]
    assert lines[4].split() == ["TOTAL", "1h", "1h45", "-", "-", "-", "-", "-", "2h45"]
    assert report.week_grid([], date(2026, 10, 5)) == "week of 2026-10-05 (2026-10-05 to 2026-10-11): no entries"


def test_csv_and_money():
    out = io.StringIO()
    report.write_csv(report.aggregate(ENTRIES, "day")[:1], ["key", "minutes", "entries"], out)
    assert out.getvalue().splitlines() == ["key,minutes,entries", "2026-10-05,60,1"]
    assert report.money(115833) == "1,158.33" and report.money(None) == "" and report.money("x") == ""
