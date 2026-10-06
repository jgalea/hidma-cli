from datetime import date, datetime, timedelta, timezone

import pytest

from hidma import api as api_mod
from hidma.client import HidmaError

from test_api import cell, row, timesheet

NOW = datetime(2026, 10, 7, 9, 0, 0)


def events(*pairs, skew_hours=0):
    """Stored events the way the API returns them: UTC stamps with a Z, plus a createdAt a second later (shifted by
    skew_hours when the stamp was sent from a browser in another zone than the tenant)."""
    out = []
    for i, (kind, at) in enumerate(pairs):
        created = datetime.strptime(at, "%Y-%m-%dT%H:%M:%S") + timedelta(hours=skew_hours, seconds=1)
        out.append({"id": f"ev-{i}", "eventType": kind, "startTime": at + ".000000Z",
                    "createdAt": created.strftime("%Y-%m-%dT%H:%M:%S") + ".000000Z"})
    return out


def test_accumulated_seconds_like_the_app():
    ev = events(("START", "2026-10-07T09:00:00"), ("PAUSE", "2026-10-07T09:10:00"), ("RESUME", "2026-10-07T09:20:00"),
                ("STOP", "2026-10-07T09:25:30"))
    assert api_mod.accumulated_seconds(ev) == 930
    running = events(("START", "2026-10-07T09:00:00"))
    assert api_mod.accumulated_seconds(running, now=datetime(2026, 10, 7, 9, 1, 15)) == 75
    paused = events(("START", "2026-10-07T09:00:00"), ("PAUSE", "2026-10-07T09:05:00"))
    assert api_mod.accumulated_seconds(paused, now=datetime(2026, 10, 7, 12, 0)) == 300
    assert api_mod.accumulated_seconds([]) == 0


def test_stored_stamps_are_corrected_by_their_createdat_skew():
    stored = events(("START", "2026-10-06T19:59:10"), ("PAUSE", "2026-10-06T19:59:31"), skew_hours=1)
    assert api_mod.stamp_skew(stored) == timedelta(hours=1)
    assert api_mod.accumulated_seconds(stored) == 21
    running = events(("START", "2026-10-06T19:59:10"), skew_hours=1)
    assert api_mod.accumulated_seconds(running, now=datetime(2026, 10, 6, 21, 0, 10)) == 60
    assert api_mod.stamp_skew(events(("START", "2026-10-06T19:59:10"))) == timedelta(0)
    assert api_mod.stamp_skew([{"eventType": "START", "startTime": "2026-10-06T19:59:10"}]) == timedelta(0)


def test_fresh_local_stamp_lines_up_with_stored_utc_stamps():
    local = datetime(2026, 10, 7, 9, 20, 10)
    stored_start = (local - timedelta(minutes=20, seconds=10)).astimezone(timezone.utc).replace(tzinfo=None)
    ev = events(("START", stored_start.strftime("%Y-%m-%dT%H:%M:%S"))) + [{"eventType": "STOP", "startTime": api_mod.local_stamp(local)}]
    assert api_mod.accumulated_seconds(ev) == 1210


def test_round_seconds_follows_tenant_setting():
    assert api_mod.round_seconds(610, {"enabled": False, "minutes": 10, "direction": "up"}) == 610
    assert api_mod.round_seconds(610, {"enabled": True, "minutes": 10, "direction": "up"}) == 1200
    assert api_mod.round_seconds(610, {"enabled": True, "minutes": 10, "direction": "down"}) == 600
    assert api_mod.round_seconds(610, None) == 610


def test_timer_helpers():
    t = {"timerEvents": events(("START", "2026-10-06T23:50:00"), ("PAUSE", "2026-10-07T00:10:00")), "timesheetRowCellId": "cell-9"}
    started_utc = datetime(2026, 10, 6, 23, 50).replace(tzinfo=timezone.utc)
    assert api_mod.timer_state(t) == "PAUSE" and api_mod.timer_cell_id(t) == "cell-9"
    assert api_mod.timer_started_at(t) == started_utc.astimezone().replace(tzinfo=None)
    assert api_mod.timer_day(t) == started_utc.astimezone().date()
    assert api_mod.timer_cell_id({"timesheetRow": {"cells": [{"id": "c-new"}]}}) == "c-new"
    assert api_mod.timer_cell_id({"timesheetRowCell": {"id": "c-2"}}) == "c-2"
    assert api_mod.timer_state({}) == "" and api_mod.timer_cell_id({}) is None and api_mod.timer_day({}) == date.today()
    assert api_mod.timer_started_at({}) is None


@pytest.fixture
def hidma(client, opener):
    return api_mod.Hidma(client), opener


@pytest.fixture
def today_is_wed(monkeypatch):
    monkeypatch.setattr(api_mod, "date", type("D", (), {"today": staticmethod(lambda: NOW.date())}))


def test_start_timer_creates_row_when_week_has_none_for_the_pair(hidma, today_is_wed):
    h, opener = hidma
    ts = timesheet([row([cell(day="2026-10-06")])])
    timers = []
    opener.route("GET", "users/user-1/timesheets", lambda c: (200, {"data": [ts]}))
    opener.route("GET", "users/user-1/timers", lambda c: (200, {"timers": timers, "timesheet": {"id": "ts-1"}}))
    opener.route("POST", "timesheets/ts-1/timers/", lambda c: (timers.append(dict(c["body"])), (200, {"result": 1}))[1])
    result = h.start_timer(project={"id": "p-2"}, job={"id": "j-1"}, comments="ZZTEST", billable=False, now=NOW)
    body = next(c for c in opener.calls if c["method"] == "POST")["body"]
    assert body["comments"] == "ZZTEST" and body["notBillable"] is True
    assert body["timerEvents"][0]["eventType"] == "START" and body["timerEvents"][0]["startTime"] == "2026-10-07T09:00:00"
    new_row = body["timesheetRow"]
    assert new_row["project"] == "p-2" and new_row["job"] == "j-1" and new_row["cells"] == [{"id": result["cell_id"], "minutes": 0,
                                                                                            "date": "2026-10-07T09:00:00"}]
    assert "timesheetRowCellId" not in body and result["timer"]["id"] == body["id"] and result["base_minutes"] == 0


def test_start_timer_links_todays_cell_or_adds_one_to_the_row(hidma, today_is_wed):
    h, opener = hidma
    ts = timesheet([row([cell(day="2026-10-07", minutes=30)])])
    timers = []
    opener.route("GET", "users/user-1/timesheets", lambda c: (200, {"data": [ts]}))
    opener.route("GET", "users/user-1/timers", lambda c: (200, {"timers": timers, "timesheet": {"id": "ts-1"}}))
    opener.route("POST", "timesheets/ts-1/timers/", lambda c: (timers.append(dict(c["body"])), (200, {"result": 1}))[1])
    result = h.start_timer(project={"id": "p-1"}, job={"id": "j-1"}, comments="", now=NOW)
    body = opener.calls[-2]["body"]
    assert body["timesheetRowCellId"] == "cell-1" and result["base_minutes"] == 30 and result["cell_id"] == "cell-1"

    timers.clear()
    opener.calls.clear()
    ts["rows"][0]["cells"] = [cell(day="2026-10-06")]
    result = h.start_timer(project={"id": "p-1"}, job={"id": "j-1"}, comments="", now=NOW)
    body = next(c for c in opener.calls if c["method"] == "POST")["body"]
    assert body["timesheetRowCell"] == {"id": result["cell_id"], "rowId": "row-1", "minutes": 0, "date": "2026-10-07T09:00:00"}


def test_start_timer_refuses_when_one_is_live(hidma):
    h, opener = hidma
    live = {"id": "tm-1", "timerEvents": events(("START", "2026-10-07T08:00:00"))}
    opener.route("GET", "users/user-1/timers", lambda c: (200, {"timers": [live], "timesheet": {"id": "ts-1"}}))
    with pytest.raises(HidmaError, match="already start"):
        h.start_timer(project={"id": "p-1"}, job={"id": "j-1"}, comments="")
    assert h.active_timer()["timesheet_id"] == "ts-1"
    live["timerEvents"] += events(("STOP", "2026-10-07T08:30:00"))
    assert h.active_timer() is None


def stop_scenario(opener, ts, server_writes: int | None):
    """Routes for a stop: the week, the STOP post, the stored timer read back with UTC stamps, settings and the cell."""
    timer = {"id": "tm-1", "timesheet_id": "ts-1", "timesheetRowCellId": "cell-1",
             "timerEvents": events(("START", "2026-10-07T08:00:00"))}
    stored = dict(timer, timerEvents=events(("START", "2026-10-07T08:00:00"), ("STOP", "2026-10-07T08:20:10")))

    def stop(c):
        if server_writes is not None:
            ts["rows"][0]["cells"][0]["minutes"] = server_writes
        return 200, {"result": 1}

    opener.route("GET", "users/user-1/timesheets", lambda c: (200, {"data": [ts]}))
    opener.route("POST", "timesheets/ts-1/timers/tm-1/events/", stop)
    opener.route("GET", "users/user-1/timers", lambda c: (200, {"timers": [stored], "timesheet": {"id": "ts-1"}}))
    opener.route("GET", "tenants/settings/", lambda c: (200, {"settings": {"timers": {"rounding": {"enabled": True, "minutes": 15, "direction": "up"}}}}))
    opener.route("PUT", "timesheets/ts-1/rows/row-1/cells/cell-1", lambda c: (ts["rows"][0]["cells"].__setitem__(0, dict(c["body"])), (200, {"result": 1}))[1])
    opener.route("GET", "timesheets/ts-1", lambda c: (200, {"data": ts}))
    return timer


def test_stop_timer_writes_minutes_when_server_leaves_the_cell_alone(hidma):
    h, opener = hidma
    ts = timesheet([row([cell(day="2026-10-07", minutes=30)])])
    timer = stop_scenario(opener, ts, server_writes=None)
    result = h.stop_timer(timer)
    event = next(c for c in opener.calls if c["path"] == "timesheets/ts-1/timers/tm-1/events/")["body"]
    assert event["eventType"] == "STOP" and len(event["startTime"]) == 19 and len(event["id"]) == 36
    assert result["seconds"] == 1210 and result["base_minutes"] == 30 and result["expected_minutes"] == 60
    assert result["cli_wrote_minutes"] is True and result["cell"]["minutes"] == 60


def test_stop_timer_trusts_minutes_the_server_wrote(hidma):
    h, opener = hidma
    ts = timesheet([row([cell(day="2026-10-07", minutes=30)])])
    timer = stop_scenario(opener, ts, server_writes=51)
    result = h.stop_timer(timer)
    assert result["cli_wrote_minutes"] is False and result["cell"]["minutes"] == 51 and result["expected_minutes"] == 60
    assert not any(c["method"] == "PUT" for c in opener.calls)


def test_stop_without_timer_raises(hidma):
    h, opener = hidma
    opener.route("GET", "users/user-1/timers", lambda c: (200, {"timers": [], "timesheet": None}))
    with pytest.raises(HidmaError, match="no running timer"):
        h.stop_timer()


def test_discard_timer_deletes_and_reports_cell(hidma):
    h, opener = hidma
    ts = timesheet([row([cell(day="2026-10-07", minutes=0)])])
    timers = [{"id": "tm-1", "timesheetRowCellId": "cell-1", "timerEvents": events(("START", "2026-10-07T09:00:00"))}]
    opener.route("DELETE", "timesheets/ts-1/timers/tm-1", lambda c: (timers.clear(), (200, {"result": 1}))[1])
    opener.route("GET", "users/user-1/timers", lambda c: (200, {"timers": timers, "timesheet": {"id": "ts-1"}}))
    opener.route("GET", "users/user-1/timesheets", lambda c: (200, {"data": [ts]}))
    result = h.discard_timer(dict(timers[0], timesheet_id="ts-1"))
    assert result == {"timer_id": "tm-1", "gone": True, "cell_id": "cell-1", "cell_minutes": 0, "cell_present": True}


def test_pause_resume_and_update_bodies(hidma):
    h, opener = hidma
    timer = {"id": "tm-1", "timesheet_id": "ts-1", "timerEvents": []}
    opener.route("POST", "timesheets/ts-1/timers/tm-1/events/", lambda c: (200, {"result": 1}))
    opener.route("PUT", "timesheets/ts-1/timers/tm-1", lambda c: (200, {"result": 1}))
    h.timer_event(timer, api_mod.TIMER_PAUSE, now=NOW)
    h.timer_event(timer, api_mod.TIMER_RESUME, now=NOW)
    h.update_timer(timer, comments="later", billable=False)
    kinds = [c["body"]["eventType"] for c in opener.calls if c["method"] == "POST"]
    assert kinds == ["PAUSE", "RESUME"]
    assert opener.calls[-1]["body"] == {"id": "tm-1", "comments": "later", "notBillable": True}


def test_favourites_round_trip(hidma):
    h, opener = hidma
    favs = []
    opener.route("GET", "favourite-timers", lambda c: (200, favs))
    opener.route("POST", "favourite-timers", lambda c: (favs.append(dict(c["body"], project={"id": "p-1", "name": "Acme website"})), (200, {"result": 1}))[1])
    fav = h.add_favourite("p-1", "j-1")
    body = next(c for c in opener.calls if c["method"] == "POST")["body"]
    assert body == {"id": fav["id"], "projectId": "p-1", "jobId": "j-1"}
    opener.route("DELETE", f"favourite-timers/{fav['id']}", lambda c: (favs.clear(), (200, {"result": 1}))[1])
    assert h.delete_favourite(fav["id"]) is True
    assert h.favourites() == []
