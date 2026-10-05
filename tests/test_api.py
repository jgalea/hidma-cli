from datetime import date

import pytest

from hidma import api as api_mod
from hidma.client import HidmaError

MONDAY = date(2026, 10, 5)
WED = date(2026, 10, 7)


def timesheet(rows=None):
    return {"id": "ts-1", "user": "user-1", "status": "st-open", "startDate": "2026-10-05T00:00:00.000Z",
            "endDate": "2026-10-11T00:00:00.000Z", "rows": rows or []}


def row(cells=None, project="p-1", job="j-1"):
    return {"id": "row-1", "timesheetId": "ts-1", "job": {"id": job, "name": "Consulting", "type": {"shortcode": "b"}},
            "project": {"id": project, "name": "Acme website", "client": {"id": "c-1", "name": "Acme"}} if project else None,
            "cells": cells or []}


def cell(day="2026-10-07", minutes=30, nb=0, comments="old"):
    return {"id": "cell-1", "date": f"{day}T00:00:00.000Z", "minutes": minutes, "notBillable": nb, "comments": comments}


@pytest.fixture
def hidma(client, opener):
    return api_mod.Hidma(client), opener


def test_log_posts_new_row_when_week_has_timesheet_but_no_row(hidma):
    h, opener = hidma
    state = {"ts": timesheet()}
    opener.route("GET", "users/user-1/timesheets", lambda c: (200, {"data": [state["ts"]]}))

    def add_row(c):
        body = c["body"]
        state["ts"] = timesheet([dict(body, cells=[dict(body["cells"][0])])])
        return 200, {"result": 1}

    opener.route("POST", "timesheets/ts-1/rows/", add_row)
    opener.route("GET", "timesheets/ts-1", lambda c: (200, {"data": state["ts"]}))

    result = h.log(project={"id": "p-1"}, job={"id": "j-1"}, day=WED, minutes=90, comments="planning", billable=True)
    post = next(c for c in opener.calls if c["method"] == "POST")
    body = post["body"]
    assert body["project"] == {"id": "p-1"} and body["job"] == {"id": "j-1"} and body["timesheetId"] == "ts-1"
    assert body["comments"] is None and body["billedMinutes"] == 0 and len(body["id"]) == 36
    c0 = body["cells"][0]
    assert c0["minutes"] == 90 and c0["notBillable"] == 0 and c0["comments"] == "planning"
    assert c0["date"] == "2026-10-07T00:00:00.000Z" and c0["pendingOperation"] == "ADD"
    assert "idempotency-key" in post["headers"]
    assert result["action"] == "new-row" and result["minutes"] == 90 and result["date"] == "2026-10-07"


def test_log_adds_cell_to_existing_row(hidma):
    h, opener = hidma
    ts = timesheet([row([cell(day="2026-10-06")])])
    opener.route("GET", "users/user-1/timesheets", lambda c: (200, {"data": [ts]}))

    def add_cell(c):
        ts["rows"][0]["cells"].append(dict(c["body"]))
        return 200, {"result": 1}

    opener.route("POST", "timesheets/rows/row-1/cells/", add_cell)
    opener.route("GET", "timesheets/ts-1", lambda c: (200, {"data": ts}))
    result = h.log(project={"id": "p-1"}, job={"id": "j-1"}, day=WED, minutes=15, comments="x", billable=False)
    post = next(c for c in opener.calls if c["method"] == "POST")
    assert post["body"]["rowId"] == "row-1" and post["body"]["minutes"] == 15 and post["body"]["notBillable"] == 15
    assert result["action"] == "new-cell" and result["not_billable"] == 15


def test_log_merges_into_existing_cell(hidma):
    h, opener = hidma
    ts = timesheet([row([cell(minutes=30, comments="old")])])
    opener.route("GET", "users/user-1/timesheets", lambda c: (200, {"data": [ts]}))

    def put(c):
        ts["rows"][0]["cells"][0] = dict(c["body"])
        return 200, {"result": 1}

    opener.route("PUT", "timesheets/ts-1/rows/row-1/cells/cell-1", put)
    opener.route("GET", "timesheets/ts-1", lambda c: (200, {"data": ts}))
    result = h.log(project={"id": "p-1"}, job={"id": "j-1"}, day=WED, minutes=45, comments="new", billable=True)
    put_call = next(c for c in opener.calls if c["method"] == "PUT")
    assert put_call["body"]["minutes"] == 75 and put_call["body"]["comments"] == "old new"
    assert put_call["body"]["id"] == "cell-1" and put_call["body"]["rowId"] == "row-1"
    assert result["action"] == "merged" and result["minutes"] == 75


def test_log_creates_timesheet_when_week_is_empty(hidma):
    h, opener = hidma
    state = {"ts": None}
    opener.route("GET", "users/user-1/timesheets", lambda c: (200, {"data": [state["ts"]] if state["ts"] else []}))
    opener.route("GET", "tenants/tenant-1/types/", lambda c: (200, {"data": {"timesheetStates": [
        {"id": "st-a", "shortcode": "a"}, {"id": "st-open", "shortcode": "o"}]}}))

    def create(c):
        state["ts"] = dict(timesheet(), id=c["body"]["id"])
        return 200, {"result": 1}

    opener.route("POST", "users/user-1/timesheets/", create)
    opener.route("POST", "timesheets/%s/rows/" % "x", lambda c: (200, {"result": 1}))

    def add_row(c):
        state["ts"]["rows"] = [dict(c["body"])]
        return 200, {"result": 1}

    opener.routes[("POST", "timesheets/" + "any")] = add_row

    # the row path carries the fresh id, so route lazily
    orig_open = opener.https_open

    def open_(req):
        if req.get_method() == "POST" and req.full_url.endswith("/rows/") and state["ts"]:
            opener.route("POST", f"timesheets/{state['ts']['id']}/rows/", add_row)
            opener.route("GET", f"timesheets/{state['ts']['id']}", lambda c: (200, {"data": state["ts"]}))
        return orig_open(req)

    opener.https_open = open_
    result = h.log(project={"id": "p-1"}, job={"id": "j-1"}, day=MONDAY, minutes=60, comments="first", billable=True)
    create_call = next(c for c in opener.calls if c["path"] == "users/user-1/timesheets/")
    b = create_call["body"]
    assert b["status"] == "st-open" and b["user"] == "user-1" and b["rows"] == [] and b["timesheetLoaded"] is True
    assert b["startDate"].startswith("2026-10-05") and b["endDate"].startswith("2026-10-11")
    assert result["action"] == "new-row"


def test_log_fails_clearly_without_open_state(hidma):
    h, opener = hidma
    opener.route("GET", "users/user-1/timesheets", lambda c: (200, {"data": []}))
    opener.route("GET", "tenants/tenant-1/types/", lambda c: (200, {"data": {}}))
    with pytest.raises(HidmaError, match="open timesheet state"):
        h.log(project={"id": "p-1"}, job={"id": "j-1"}, day=MONDAY, minutes=60, comments="x")


def test_delete_cell_then_empty_row(hidma):
    h, opener = hidma
    ts = timesheet([row([cell()])])
    entry = api_mod.flatten(ts)[0]
    opener.route("DELETE", "timesheets/ts-1/rows/cells/", lambda c: (ts["rows"][0]["cells"].clear(), (200, {"result": 1}))[1])
    opener.route("DELETE", "timesheets/ts-1/rows/", lambda c: (ts["rows"].clear(), (200, {"result": 1}))[1])
    opener.route("GET", "timesheets/ts-1", lambda c: (200, {"data": ts}))
    result = h.delete_cell(entry)
    deletes = [c for c in opener.calls if c["method"] == "DELETE"]
    assert deletes[0]["path"] == 'timesheets/ts-1/rows/cells/?cellIds=%5B%22cell-1%22%5D'
    assert deletes[1]["path"] == 'timesheets/ts-1/rows/?rowIds=%5B%22row-1%22%5D'
    assert result == {"cell_id": "cell-1", "row_deleted": True, "gone": True}


def test_delete_keeps_row_with_other_cells(hidma):
    h, opener = hidma
    ts = timesheet([row([cell(), dict(cell(day="2026-10-08"), id="cell-2")])])
    entry = api_mod.flatten(ts)[0]
    opener.route("DELETE", "timesheets/ts-1/rows/cells/", lambda c: (ts["rows"][0]["cells"].pop(0), (200, {"result": 1}))[1])
    opener.route("GET", "timesheets/ts-1", lambda c: (200, {"data": ts}))
    result = h.delete_cell(entry)
    assert result["row_deleted"] is False and result["gone"] is True
    assert all(c["path"] != "timesheets/ts-1/rows/" for c in opener.calls)


def test_update_cell_body(hidma):
    h, opener = hidma
    ts = timesheet([row([cell(minutes=30, nb=30)])])
    entry = api_mod.flatten(ts)[0]
    opener.route("PUT", "timesheets/ts-1/rows/row-1/cells/cell-1", lambda c: (ts["rows"][0]["cells"].__setitem__(0, dict(c["body"])), (200, {"result": 1}))[1])
    opener.route("GET", "timesheets/ts-1", lambda c: (200, {"data": ts}))
    result = h.update_cell(entry, minutes=60, comments="changed")
    body = next(c for c in opener.calls if c["method"] == "PUT")["body"]
    assert body["minutes"] == 60 and body["notBillable"] == 60 and body["comments"] == "changed" and body["rowId"] == "row-1"
    assert result["action"] == "updated"


def test_entries_and_totals(hidma):
    h, opener = hidma
    ts = timesheet([row([cell(day="2026-10-06", minutes=60), dict(cell(day="2026-10-07", minutes=30), id="c2")]),
                    dict(row([dict(cell(day="2026-10-06", minutes=15), id="c3")], project=None, job="j-2"), id="row-2")])
    opener.route("GET", "users/user-1/timesheets", lambda c: (200, {"data": [ts]}))
    entries = h.entries(date(2026, 10, 6), date(2026, 10, 7))
    assert [e["id"] for e in entries] == ["c3", "cell-1", "c2"]
    t = api_mod.totals(entries)
    assert t["by_day"] == {"2026-10-06": 75, "2026-10-07": 30} and t["total"] == 105
    assert t["by_project"] == {"Acme / Acme website / Consulting": 90, "Consulting": 15}


def test_find_row_matches_project_and_job():
    ts = timesheet([row(), dict(row(project=None, job="j-2"), id="row-2")])
    assert api_mod.find_row(ts, "p-1", "j-1")["id"] == "row-1"
    assert api_mod.find_row(ts, None, "j-2")["id"] == "row-2"
    assert api_mod.find_row(ts, None, "j-1") is None
    assert api_mod.find_row(ts, "p-2", "j-1") is None


def test_list_all_walks_pages(hidma):
    h, opener = hidma
    pages = {1: ([{"id": "a"}, {"id": "b"}], {"lastPage": 2}), 2: ([{"id": "c"}], {"lastPage": 2})}

    def clients(c):
        page = int(c["path"].split("page=")[1].split("&")[0])
        items, pd = pages[page]
        return 200, {"data": {"data": items, "pageData": pd}}

    opener.route("GET", "clients/", clients)
    assert [x["id"] for x in h.clients()] == ["a", "b", "c"]
