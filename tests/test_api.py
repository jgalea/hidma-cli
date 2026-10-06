import urllib.parse
from datetime import date, timedelta

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
    opener.route("GET", "tenants/types/", lambda c: (200, {"data": {"timesheetStates": [
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
    opener.route("GET", "tenants/types/", lambda c: (200, {"data": {}}))
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


TYPES = {"timesheetStates": [{"id": "st-open", "shortcode": "o", "name": "Open"}, {"id": "st-s", "shortcode": "s", "name": "Submitted"}],
         "jobTypes": [{"id": 1, "shortcode": "b"}, {"id": 2, "shortcode": "i"}, {"id": 3, "shortcode": "p"}],
         "clientTypes": [{"id": 1, "shortcode": "u"}, {"id": 2, "shortcode": "c"}, {"id": 3, "shortcode": "s"}],
         "clientStates": [{"id": 1, "shortcode": "a"}, {"id": 2, "shortcode": "i"}, {"id": 3, "shortcode": "c"}],
         "projectTypes": [{"id": 1, "shortcode": "b"}, {"id": 2, "shortcode": "i"}],
         "projectStates": [{"id": 1, "shortcode": "a"}, {"id": 2, "shortcode": "i"}, {"id": 3, "shortcode": "c"}]}


@pytest.fixture
def typed(hidma):
    h, opener = hidma
    opener.route("GET", "tenants/types/", lambda c: (200, {"data": TYPES}))
    return h, opener


def test_types_are_fetched_once_and_resolve_shortcodes(typed):
    h, opener = typed
    assert h.job_shortcode({"type": 1}) == "b" and h.job_shortcode({"type": {"shortcode": "P"}}) == "p"
    assert h.job_shortcode({"type": 99}) == ""
    assert h.type_by_shortcode("clientStates", "c")["id"] == 3
    assert h.timesheet_state_name({"status": "st-s"}) == "submitted"
    with pytest.raises(HidmaError, match="no jobTypes entry"):
        h.type_by_shortcode("jobTypes", "z")
    assert len([c for c in opener.calls if c["path"].startswith("tenants/types/")]) == 1


def test_teams_and_users(hidma):
    h, opener = hidma
    opener.route("GET", "teams/", lambda c: (200, {"teams": [{"id": "t-1", "name": "Core"}]}))
    opener.route("GET", "users/", lambda c: (200, {"pageData": {"lastPage": 1}, "data": [{"id": "user-1", "name": "Ann", "teamId": "t-1"}],
                                                   "teams": []}))
    assert h.teams() == [{"id": "t-1", "name": "Core"}]
    assert h.users()[0]["teamId"] == "t-1"


def test_add_client_body_and_read_back(typed):
    h, opener = typed
    created = {}

    def post(c):
        created.update(c["body"])
        return 200, {"result": 1}

    opener.route("POST", "clients/", post)
    opener.route("GET", "clients/", lambda c: (200, {"data": [dict(created)]}))
    client = h.add_client("Acme", type_shortcode="s", vat="MT123")
    assert created["name"] == "Acme" and created["type"] == 3 and created["status"] == 1 and created["vatNumber"] == "MT123"
    assert created["contacts"] == [] and created["addresses"] == [] and created["chargeOutRates"] is None and len(created["id"]) == 36
    assert client["id"] == created["id"]


def test_update_client_keeps_nested_lists(typed):
    h, opener = typed
    current = {"id": "c-1", "name": "Acme", "status": {"id": 1, "shortcode": "a"}, "type": {"id": 2}, "avatarColour": "#fff",
               "contacts": [{"id": "ct-1"}], "addresses": [], "vatNumber": "", "defaultTaxRate": None}
    opener.route("GET", "clients/c-1", lambda c: (200, {"data": current}))
    opener.route("PUT", "clients/c-1", lambda c: (200, {"result": 1}))
    opener.route("GET", "clients/", lambda c: (200, {"data": [{"id": "c-1", "name": "Acme Ltd"}]}))
    h.update_client("c-1", name="Acme Ltd", state_shortcode="c")
    body = next(c for c in opener.calls if c["method"] == "PUT")["body"]
    assert body["name"] == "Acme Ltd" and body["status"] == 3 and body["type"] == 2 and body["contacts"] == [{"id": "ct-1"}]
    assert body["avatarColour"] == "#fff" and body["comments"] == []


def test_add_project_body(typed):
    h, opener = typed
    created = {}

    def post(c):
        created.update(c["body"])
        opener.route("GET", f"projects/stats/{c['body']['id']}/", lambda c: (200, {"data": [dict(created, stats={})]}))
        return 200, {"result": 1}

    opener.route("POST", "projects/", post)
    project = h.add_project("Website", client_id="c-1", job_ids=["j-1", "j-2"], team_id="t-1")
    assert created["client"] == "c-1" and created["jobs"] == ["j-1", "j-2"] and created["team"] == "t-1"
    assert created["type"] == 1 and created["status"] == 1 and created["budgets"] == [] and created["template"] is None
    assert project["id"] == created["id"]
    with pytest.raises(HidmaError, match="needs a client"):
        h.add_project("Nope", client_id=None, job_ids=[], team_id="t-1")


def test_update_project_adds_and_removes_jobs(typed):
    h, opener = typed
    current = {"id": "p-1", "name": "Website", "type": {"id": 1}, "team": "t-1", "status": {"id": 1}, "billingTypeId": 1,
               "jobs": [{"id": "j-1"}, {"id": "j-2"}], "client": {"id": "c-1"}, "budgets": [], "startDate": None,
               "budgetNotificationsEnabled": True, "budgetNotifications": [], "description": None, "targetRecoverability": None}
    opener.route("GET", "projects/stats/p-1/", lambda c: (200, {"data": [current]}))
    opener.route("PUT", "projects/p-1", lambda c: (200, {"result": 1}))
    h.update_project("p-1", add_job_ids=["j-3"], remove_job_ids=["j-1"], state_shortcode="c")
    body = next(c for c in opener.calls if c["method"] == "PUT")["body"]
    assert body["jobs"] == ["j-2", "j-3"] and body["status"] == 3 and body["client"] == "c-1" and body["billingType"] == 1


def test_add_and_update_job_bodies(typed):
    h, opener = typed
    store = {}
    opener.route("POST", "jobs/", lambda c: (store.update(c["body"]), (200, {"result": 1}))[1])
    opener.route("GET", f"jobs/{'any'}/", lambda c: (200, {}))
    orig = opener.https_open

    def open_(req):
        if "/jobs/" in req.full_url and req.get_method() == "GET" and store:
            opener.route("GET", f"jobs/{store['id']}/", lambda c: (200, {"data": [dict(store, type={"id": store["type"]}, teams=[{"id": "t-1"}])]}))
        return orig(req)

    opener.https_open = open_
    job = h.add_job("Design", type_shortcode="i")
    assert store["type"] == 2 and store["active"] is True and store["defaultTaxRate"] == 1 and store["taxRates"] == 1
    assert job["id"] == store["id"]
    opener.route("PUT", f"jobs/{store['id']}", lambda c: (200, {"result": 1}))
    h.update_job(store["id"], active=False, name="Design work")
    body = next(c for c in opener.calls if c["method"] == "PUT")["body"]
    assert body["active"] is False and body["name"] == "Design work" and body["type"] == 2 and body["teams"] == ["t-1"]


def test_raw_report_params_and_pages(hidma):
    h, opener = hidma
    pages = {0: ([{"project": {"name": "A"}, "minutes": 60}], {"lastPage": 1}), 1: ([{"project": {"name": "B"}, "minutes": 30}], {"lastPage": 1})}

    def report(c):
        page = int(c["path"].split("page=")[1].split("&")[0])
        items, pd = pages[page]
        return 200, {"data": {"data": items, "pageData": pd, "totals": {"totalMinutes": 90, "totalCost": 1000}}}

    opener.route("GET", "reports/raw/", report)
    out = h.raw_report("unbilled", date(2026, 1, 1), date(2026, 1, 31), client_ids=["c-1"])
    assert [r["minutes"] for r in out["rows"]] == [60, 30] and out["totals"]["totalMinutes"] == 90
    first = urllib.parse.unquote_plus(opener.calls[0]["path"])
    assert "type=unbilled" in first and "billed=false" in first and "page=0" in first
    assert 'filters={"reporting":{"dateRange":{"start":"2026-01-01","end":"2026-01-31"},"clients":["c-1"]}}' in first
    assert len(opener.calls) == 2
    with pytest.raises(HidmaError, match="report type"):
        h.raw_report("nope")


def test_recent_entries_walks_back(hidma):
    h, opener = hidma
    this_week = api_mod.monday_of(date.today())
    weeks = {}
    for back, ids in ((0, ["n1"]), (1, ["o1", "o2"])):
        monday = this_week - timedelta(days=7 * back)
        weeks[str(monday)] = timesheet([row([dict(cell(day=str(monday + timedelta(days=i))), id=i_d) for i, i_d in enumerate(ids)])])

    def week(c):
        start = c["path"].split("startDate=")[1][:10]
        return 200, {"data": [weeks[start]] if start in weeks else []}

    opener.route("GET", "users/user-1/timesheets", week)
    assert [e["id"] for e in h.recent_entries(2)] == ["o2", "n1"]
    assert [e["id"] for e in h.recent_entries(10)] == ["o1", "o2", "n1"]
