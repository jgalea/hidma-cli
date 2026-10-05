"""What the CLI needs from hidma, built on the endpoints the web app uses (see docs/API.md).

Time in hidma is a weekly timesheet of rows (project + job) with one cell per day holding
minutes. "Log 1h30 on project X" therefore means: find the week's timesheet (create it if
the week has none), find the row for that project and its job (create it if missing),
then add or merge the day's cell.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .client import Client, HidmaError, new_id

PAGE_SIZE = 100
MAX_PAGES = 50
PENDING_ADD = "ADD"


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def iso_date(value) -> str:
    """YYYY-MM-DD from a date or an API date string (ISO datetime or date)."""
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def _page_items(payload) -> tuple[list, dict]:
    """Rows and pageData from the envelope shapes the app handles."""
    if isinstance(payload, list):
        return payload, {}
    if isinstance(payload, dict):
        inner = payload
        if isinstance(payload.get("data"), dict) and "data" in payload["data"]:
            inner = payload["data"]
        if isinstance(inner.get("data"), list):
            return inner["data"], inner.get("pageData") or {}
        for key in ("timesheets", "clients", "projects", "jobs", "timers", "items"):
            if isinstance(inner.get(key), dict) and isinstance(inner[key].get("data"), list):
                return inner[key]["data"], inner[key].get("pageData") or {}
            if isinstance(inner.get(key), list):
                return inner[key], {}
    return [], {}


def _single_timesheet(payload) -> dict | None:
    """The live API wraps one week as {"data": {"timesheet": {...}}}."""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("timesheet"), dict):
            return data["timesheet"]
        if isinstance(payload.get("timesheet"), dict):
            return payload["timesheet"]
    return None


class Hidma:
    def __init__(self, client: Client | None = None):
        self.client = client or Client()

    @property
    def user_id(self) -> str:
        return self.client.user_id

    # -- reads ------------------------------------------------------------

    def me(self) -> dict:
        return self.client.get(f"users/{self.user_id}", source="userEffects.fetchUser$") or {}

    def _list_all(self, path: str, params: dict, source: str) -> list[dict]:
        """Walk ?page=1..lastPage. Stops when a page repeats or comes back empty."""
        out, seen = [], set()
        for page in range(1, MAX_PAGES + 1):
            items, page_data = _page_items(self.client.get(path, dict(params, page=page, size=PAGE_SIZE), source=source))
            fresh = [it for it in items if it.get("id") not in seen]
            if not fresh:
                break
            seen.update(it.get("id") for it in fresh)
            out.extend(fresh)
            last = page_data.get("lastPage")
            if (page >= int(last)) if last is not None else (len(items) < PAGE_SIZE):
                break
        return out

    def clients(self, term: str | None = None) -> list[dict]:
        return self._list_all("clients/", {"filter": term}, "client.service.getClientsBatch")

    def projects(self, client_id: str | None = None, term: str | None = None) -> list[dict]:
        params = {"term": term, "clients": [client_id] if client_id else None}
        return self._list_all("projects/", params, "projects.service.fetchProjectBatch")

    def jobs(self, term: str | None = None) -> list[dict]:
        return self._list_all("jobs/", {"filter": term}, "jobs.service.page")

    def project_jobs(self, term: str, client_id: str | None = None, size: int = 25) -> list[dict]:
        payload = self.client.get("projects-jobs/autocomplete/", {"term": term, "client": client_id, "size": size, "page": 0},
                                  source="projects.service.autoCompleteProjectJob")
        items, _ = _page_items(payload)
        return items if items else (payload if isinstance(payload, list) else [])

    def week_timesheet(self, day: date) -> dict | None:
        """The user's timesheet covering `day`, rows and cells included; None when the week has none."""
        start = monday_of(day)
        payload = self.client.get(f"users/{self.user_id}/timesheets", {"startDate": iso_date(start)},
                                  source="TimesheetsEffects.fetchTimesheetByDate$")
        single = _single_timesheet(payload)
        if single is not None:
            return single
        items, _ = _page_items(payload)
        if not items and isinstance(payload, dict) and payload.get("id"):
            items = [payload]
        for ts in items:
            if iso_date(ts.get("startDate")) <= iso_date(day) <= iso_date(ts.get("endDate")):
                return ts
        return items[0] if items else None

    def timesheet(self, timesheet_id: str) -> dict:
        payload = self.client.get(f"timesheets/{timesheet_id}", {"withUser": 1}, source="TimesheetsEffects.fetchTimesheet$")
        single = _single_timesheet(payload)
        if single is not None:
            return single
        items, _ = _page_items(payload)
        return items[0] if items else payload

    def timesheet_states(self) -> list[dict]:
        """The tenant's timesheet states (shortcode o/s/a)."""
        payload = self.client.get(f"tenants/{self.client.tenant_id}/types/", source="tenant.service.types")
        if isinstance(payload, dict):
            for key in ("timesheetStates", "timesheet_states"):
                if isinstance(payload.get(key), list):
                    return payload[key]
        return []

    def entries(self, start: date, end: date) -> list[dict]:
        """Every cell between start and end inclusive, flattened with its row's project/job and the timesheet id."""
        out = []
        monday = monday_of(start)
        while monday <= end:
            ts = self.week_timesheet(monday)
            if ts:
                out.extend(e for e in flatten(ts) if iso_date(start) <= e["date"] <= iso_date(end))
            monday += timedelta(days=7)
        return sorted(out, key=lambda e: (e["date"], e["client"], e["project"], e["job"]))

    # -- writes -----------------------------------------------------------

    def create_timesheet(self, day: date) -> dict:
        """POST the provisional timesheet the app saves when the first row lands in an empty week."""
        states = self.timesheet_states()
        open_state = next((s for s in states if str(s.get("shortcode", "")).lower() == "o"), None)
        if open_state is None:
            raise HidmaError("cannot find the tenant's open timesheet state, so cannot create a timesheet for this week")
        start = monday_of(day)
        body = {"id": new_id(), "startDate": _iso_midnight(start), "endDate": _iso_midnight(start + timedelta(days=6)),
                "status": open_state["id"], "rows": [], "user": self.user_id, "timesheetLoaded": True}
        self.client.post(f"users/{self.user_id}/timesheets/", body, source="TimesheetsEffects.saveProvisionalTimesheet$")
        ts = self.week_timesheet(day)
        if ts is None:
            raise HidmaError("timesheet was posted but the week still reads back empty")
        return ts

    def log(self, *, project: dict | None, job: dict, day: date, minutes: int, comments: str, billable: bool = True) -> dict:
        """Add minutes to a day. Returns {"cell", "row", "timesheet", "action"} with action new-row|new-cell|merged."""
        ts = self.week_timesheet(day) or self.create_timesheet(day)
        not_billable = 0 if billable else minutes
        row = find_row(ts, project.get("id") if project else None, job["id"])
        if row is None:
            cell = {"id": new_id(), "date": _iso_midnight(day), "minutes": minutes, "notBillable": not_billable,
                    "comments": comments, "pendingOperation": PENDING_ADD}
            body = {"id": new_id(), "timesheetId": ts["id"], "project": {"id": project["id"]} if project else None,
                    "job": {"id": job["id"]}, "cells": [cell], "comments": None, "billedMinutes": 0}
            self.client.post(f"timesheets/{ts['id']}/rows/", body, source="TimesheetRowsEffects.addTimesheetRow$")
            return self._read_back(ts["id"], body["id"], cell["id"], "new-row")
        existing = find_cell(row, day)
        if existing is None:
            cell = {"id": new_id(), "date": _iso_midnight(day), "minutes": minutes, "notBillable": not_billable,
                    "comments": comments, "rowId": row["id"], "pendingOperation": PENDING_ADD}
            self.client.post(f"timesheets/rows/{row['id']}/cells/", cell, source="TimesheetRowCellsEffects.addNewCell$")
            return self._read_back(ts["id"], row["id"], cell["id"], "new-cell")
        merged = dict(existing)
        merged["minutes"] = int(existing.get("minutes") or 0) + minutes
        merged["notBillable"] = int(existing.get("notBillable") or 0) + not_billable
        merged["comments"] = _join_comments(existing.get("comments"), comments)
        merged["rowId"] = row["id"]
        self.client.put(f"timesheets/{ts['id']}/rows/{row['id']}/cells/{existing['id']}", merged,
                        source="TimesheetRowCellsEffects.updateCell$")
        return self._read_back(ts["id"], row["id"], existing["id"], "merged")

    def update_cell(self, entry: dict, *, minutes: int | None = None, comments: str | None = None,
                    billable: bool | None = None) -> dict:
        """PUT a cell found by find_entry with the given fields changed."""
        cell = dict(entry["cell"])
        if minutes is not None:
            cell["minutes"] = minutes
        if comments is not None:
            cell["comments"] = comments
        if billable is not None:
            cell["notBillable"] = 0 if billable else int(cell.get("minutes") or 0)
        elif minutes is not None and int(entry["cell"].get("notBillable") or 0) >= int(entry["cell"].get("minutes") or 0) > 0:
            cell["notBillable"] = minutes  # a fully non-billable cell stays fully non-billable
        cell["rowId"] = entry["row_id"]
        self.client.put(f"timesheets/{entry['timesheet_id']}/rows/{entry['row_id']}/cells/{cell['id']}", cell,
                        source="TimesheetRowCellsEffects.updateCell$")
        return self._read_back(entry["timesheet_id"], entry["row_id"], cell["id"], "updated")

    def delete_cell(self, entry: dict) -> dict:
        """DELETE the cell; when that leaves its row with no cells, delete the row too so the week is as before."""
        ts_id, row_id, cell_id = entry["timesheet_id"], entry["row_id"], entry["cell"]["id"]
        self.client.delete(f"timesheets/{ts_id}/rows/cells/", {"cellIds": [cell_id]},
                           source="TimesheetRowCellsEffects.deleteTimesheetCells$")
        ts = self.timesheet(ts_id)
        row = next((r for r in ts.get("rows") or [] if r.get("id") == row_id), None)
        row_deleted = False
        if row is not None and not [c for c in row.get("cells") or [] if c and not c.get("deleted_at")]:
            self.client.delete(f"timesheets/{ts_id}/rows/", {"rowIds": [row_id]}, source="TimesheetRowsEffects.deleteRowsStart$")
            row_deleted = True
            ts = self.timesheet(ts_id)
        still_there = any(c.get("id") == cell_id for r in ts.get("rows") or [] for c in r.get("cells") or [] if c)
        return {"cell_id": cell_id, "row_deleted": row_deleted, "gone": not still_there}

    def find_entry(self, cell_id: str, *, hint: date | None = None, weeks_back: int = 12) -> dict | None:
        """Locate a cell by id: the hinted week first, then this week and the previous `weeks_back` weeks."""
        mondays = []
        if hint:
            mondays.append(monday_of(hint))
        this_week = monday_of(date.today())
        mondays.extend(this_week - timedelta(days=7 * i) for i in range(weeks_back + 1))
        seen = set()
        for monday in mondays:
            if monday in seen:
                continue
            seen.add(monday)
            ts = self.week_timesheet(monday)
            if not ts:
                continue
            for e in flatten(ts):
                if e["cell"]["id"] == cell_id:
                    return e
        return None

    def _read_back(self, ts_id: str, row_id: str, cell_id: str, action: str) -> dict:
        ts = self.timesheet(ts_id)
        for e in flatten(ts):
            if e["cell"]["id"] == cell_id:
                return dict(e, action=action)
        raise HidmaError(f"the write returned OK but cell {cell_id} is not in timesheet {ts_id} when read back")

    # -- timers -----------------------------------------------------------

    def timers_for_week(self, day: date) -> dict:
        payload = self.client.get(f"users/{self.user_id}/timers", {"startDate": iso_date(monday_of(day))},
                                  source="TimeTrackersService.getTimersForWeek")
        return payload if isinstance(payload, dict) else {"timers": payload or [], "timesheet": None}


def _iso_midnight(day: date) -> str:
    return datetime(day.year, day.month, day.day).isoformat(timespec="milliseconds") + "Z"


def _join_comments(old, new) -> str:
    old = (old or "").strip()
    new = (new or "").strip()
    if old and new:
        return f"{old} {new}"
    return old or new


def find_row(ts: dict, project_id: str | None, job_id: str) -> dict | None:
    for row in ts.get("rows") or []:
        rp = row.get("project")
        rp_id = rp.get("id") if isinstance(rp, dict) else rp
        rj = row.get("job")
        rj_id = rj.get("id") if isinstance(rj, dict) else rj
        if str(rj_id) == str(job_id) and (str(rp_id) if rp_id else None) == (str(project_id) if project_id else None):
            return row
    return None


def find_cell(row: dict, day: date) -> dict | None:
    for cell in row.get("cells") or []:
        if cell and not cell.get("deleted_at") and iso_date(cell.get("date")) == iso_date(day):
            return cell
    return None


def flatten(ts: dict) -> list[dict]:
    """One record per cell: date, client, project, job, minutes, not_billable, comments, ids, and the raw cell."""
    out = []
    for row in ts.get("rows") or []:
        project = row.get("project") if isinstance(row.get("project"), dict) else None
        job = row.get("job") if isinstance(row.get("job"), dict) else {}
        client = (project or {}).get("client") if project else None
        for cell in row.get("cells") or []:
            if not cell or cell.get("deleted_at"):
                continue
            out.append({
                "id": cell.get("id"), "date": iso_date(cell.get("date")),
                "client": (client or {}).get("name", "") if isinstance(client, dict) else "",
                "project": (project or {}).get("name", "") if project else "",
                "project_id": (project or {}).get("id") if project else None,
                "job": job.get("name", ""), "job_id": job.get("id"),
                "minutes": int(cell.get("minutes") or 0), "not_billable": int(cell.get("notBillable") or 0),
                "comments": cell.get("comments") or "",
                "timesheet_id": ts.get("id"), "row_id": row.get("id"), "cell": cell,
            })
    return out


def totals(entries: list[dict]) -> dict:
    by_day: dict[str, int] = {}
    by_project: dict[str, int] = {}
    for e in entries:
        by_day[e["date"]] = by_day.get(e["date"], 0) + e["minutes"]
        key = " / ".join(x for x in (e["client"], e["project"], e["job"]) if x)
        by_project[key] = by_project.get(key, 0) + e["minutes"]
    return {"by_day": dict(sorted(by_day.items())), "by_project": dict(sorted(by_project.items(), key=lambda kv: -kv[1])),
            "total": sum(e["minutes"] for e in entries)}

