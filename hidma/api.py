"""What the CLI needs from hidma, built on the endpoints the web app uses (see docs/API.md).

Time in hidma is a weekly timesheet of rows (project + job) with one cell per day holding
minutes. "Log 1h30 on project X" therefore means: find the week's timesheet (create it if
the week has none), find the row for that project and its job (create it if missing),
then add or merge the day's cell.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .client import Client, HidmaError, new_id

PAGE_SIZE = 100
MAX_PAGES = 50
PENDING_ADD = "ADD"

# Timer event types (chunk-M55H2YJN `g`): the app posts these strings.
TIMER_START = "START"
TIMER_PAUSE = "PAUSE"
TIMER_RESUME = "RESUME"
TIMER_STOP = "STOP"
TIMER_LIVE_EVENTS = {TIMER_START, TIMER_RESUME, TIMER_PAUSE}

# Report types the raw report endpoint accepts (chunk-NDFZAGSW).
REPORT_TYPES = ("unbilled", "notBillable", "charges", "internal", "personal")
REPORT_EPOCH = date(2000, 1, 1)


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
        for key in ("timesheets", "clients", "projects", "jobs", "timers", "teams", "items"):
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
        """The user record (the live API wraps it as {user, tenants, types, defaultSettingsId})."""
        payload = self.client.get(f"users/{self.user_id}", source="userEffects.fetchUser$") or {}
        return payload["user"] if isinstance(payload.get("user"), dict) else payload

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

    def types(self) -> dict:
        """The tenant's type tables (jobTypes, projectTypes, projectStates, clientTypes, clientStates, timesheetStates...)."""
        if not hasattr(self, "_types"):
            payload = self.client.get("tenants/types/", source="TenantsEffects.fetchTenantTypes$")
            self._types = payload if isinstance(payload, dict) else {}
        return self._types

    def timesheet_states(self) -> list[dict]:
        """The tenant's timesheet states (shortcode o/s/a)."""
        states = self.types().get("timesheetStates")
        return states if isinstance(states, list) else []

    def timesheet_state_name(self, ts: dict) -> str:
        """'open', 'submitted' or 'approved' for a timesheet whose status is a state id."""
        for state in self.timesheet_states():
            if state.get("id") == ts.get("status"):
                return str(state.get("name", "")).lower()
        return ""

    def type_by_shortcode(self, table: str, shortcode: str) -> dict:
        """One row of a tenant type table by its shortcode, e.g. ('jobTypes', 'b')."""
        for row in self.types().get(table) or []:
            if str(row.get("shortcode", "")).lower() == shortcode.lower():
                return row
        raise HidmaError(f"the tenant has no {table} entry with shortcode {shortcode!r}")

    def shortcode_of(self, table: str, value) -> str:
        """The shortcode behind a type reference, which the API returns as a dict in some places and a bare id in others."""
        if isinstance(value, dict):
            return str(value.get("shortcode", "")).lower()
        for row in self.types().get(table) or []:
            if row.get("id") == value:
                return str(row.get("shortcode", "")).lower()
        return ""

    def job_shortcode(self, job: dict) -> str:
        return self.shortcode_of("jobTypes", job.get("type"))

    def settings(self) -> dict:
        payload = self.client.get("tenants/settings/", source="TenantsEffects.fetchTenantSettings$")
        if isinstance(payload, dict) and isinstance(payload.get("settings"), dict):
            return payload["settings"]
        return payload if isinstance(payload, dict) else {}

    def teams(self) -> list[dict]:
        payload = self.client.get("teams/", source="TeamsEffects.fetchTeams$")
        items, _ = _page_items(payload)
        return items

    def users(self) -> list[dict]:
        return self._list_all("users/", {}, "users.service.fetchUserByTeamSelectBatch")

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

    def active_timer(self, day: date | None = None) -> dict | None:
        """The week's timer whose last event is START, RESUME or PAUSE, with its timesheet id; None when there is none."""
        data = self.timers_for_week(day or date.today())
        ts = data.get("timesheet") or {}
        for t in data.get("timers") or []:
            if timer_state(t) in TIMER_LIVE_EVENTS:
                return dict(t, timesheet_id=ts.get("id") or t.get("timesheetId") or t.get("timesheet"))
        return None

    def start_timer(self, *, project: dict | None, job: dict, comments: str, billable: bool = True,
                    now: datetime | None = None) -> dict:
        """POST the timer the app's widget creates (chunk-M55H2YJN `Is`): it is linked to today's cell for the
        project and job, creating the row or the cell with 0 minutes when the week has none."""
        running = self.active_timer()
        if running:
            raise HidmaError(f"a timer is already {timer_state(running).lower()} ({running.get('id')}); stop or discard it first")
        now = now or datetime.now()
        today = now.date()
        ts = self.week_timesheet(today) or self.create_timesheet(today)
        body = {"id": new_id(), "comments": comments, "notBillable": not billable,
                "timerEvents": [{"id": new_id(), "eventType": TIMER_START, "startTime": local_stamp(now)}]}
        row = find_row(ts, project.get("id") if project else None, job["id"])
        cell = find_cell(row, today) if row else None
        if cell:
            body["timesheetRowCellId"] = cell["id"]
            row_id, cell_id = row["id"], cell["id"]
        elif row is None:
            row_id, cell_id = new_id(), new_id()
            body["timesheetRow"] = {"project": project["id"] if project else None, "job": job["id"], "id": row_id,
                                    "cells": [{"id": cell_id, "minutes": 0, "date": local_stamp(now)}]}
        else:
            row_id, cell_id = row["id"], new_id()
            body["timesheetRowCell"] = {"id": cell_id, "rowId": row_id, "minutes": 0, "date": local_stamp(now)}
        self.client.post(f"timesheets/{ts['id']}/timers/", body, source="TimeTrackersService.postCreateNewTimer")
        timer = next((t for t in (self.timers_for_week(today).get("timers") or []) if t.get("id") == body["id"]), None)
        if timer is None:
            raise HidmaError(f"the timer was posted but {body['id']} is not in the week's timers when read back")
        return {"timer": timer, "timesheet_id": ts["id"], "row_id": row_id, "cell_id": cell_id,
                "base_minutes": int((cell or {}).get("minutes") or 0)}

    def timer_event(self, timer: dict, event_type: str, now: datetime | None = None) -> dict:
        """Append START/PAUSE/RESUME/STOP to a timer (TimeTrackersService.postCreateEventsWithTimerId)."""
        ts_id = timer["timesheet_id"]
        event = {"id": new_id(), "eventType": event_type, "startTime": local_stamp(now or datetime.now())}
        self.client.post(f"timesheets/{ts_id}/timers/{timer['id']}/events/", event,
                         source="TimeTrackersService.postCreateEventsWithTimerId")
        return event

    def stop_timer(self, timer: dict | None = None) -> dict:
        """STOP the active timer and make sure its minutes land in the cell.

        On STOP the server adds the tracked minutes to the linked cell itself (verified live; it also copies the
        timer's comment into the cell). The app computes the same sum client-side, rounded per the tenant's
        timers.rounding setting. This reads the cell back and writes that sum only when the server left the cell
        untouched. `seconds` comes from the events as the server stored them after the STOP.
        """
        timer = timer or self.active_timer()
        if timer is None:
            raise HidmaError("no running timer")
        cell_id = timer_cell_id(timer)
        day = timer_day(timer)
        before = self.find_entry(cell_id, hint=day, weeks_back=1) if cell_id else None
        base = int(before["minutes"]) if before else 0
        event = self.timer_event(timer, TIMER_STOP)
        stored = next((t for t in (self.timers_for_week(day).get("timers") or []) if t.get("id") == timer["id"]), None)
        events = (stored or {}).get("timerEvents") or list(timer.get("timerEvents") or []) + [event]
        seconds = accumulated_seconds(events)
        rounding = (self.settings().get("timers") or {}).get("rounding") or {}
        expected = round_seconds(base * 60 + seconds, rounding) // 60
        after = self.find_entry(cell_id, hint=day, weeks_back=1) if cell_id else None
        wrote = False
        if after is not None and int(after["minutes"]) == base and expected != base:
            self.update_cell(after, minutes=expected)
            after = self.find_entry(cell_id, hint=day, weeks_back=1)
            wrote = True
        return {"timer_id": timer["id"], "seconds": seconds, "base_minutes": base, "expected_minutes": expected,
                "cell": {k: v for k, v in (after or {}).items() if k != "cell"} if after else None,
                "cli_wrote_minutes": wrote}

    def discard_timer(self, timer: dict | None = None) -> dict:
        """DELETE a live timer (the app's Cancel: tracked time is dropped). Returns whether its cell survived; a cell
        the timer created with 0 minutes goes with it. The API refuses to delete a timer once it is stopped."""
        timer = timer or self.active_timer()
        if timer is None:
            raise HidmaError("no running timer")
        self.client.delete(f"timesheets/{timer['timesheet_id']}/timers/{timer['id']}",
                           source="TimeTrackersService.deleteTimer")
        day = timer_day(timer)
        still = any(t.get("id") == timer["id"] for t in (self.timers_for_week(day).get("timers") or []))
        cell_id = timer_cell_id(timer)
        cell = self.find_entry(cell_id, hint=day, weeks_back=1) if cell_id else None
        return {"timer_id": timer["id"], "gone": not still, "cell_id": cell_id,
                "cell_minutes": cell["minutes"] if cell else None, "cell_present": cell is not None}

    def update_timer(self, timer: dict, *, comments: str | None = None, billable: bool | None = None) -> None:
        body = {"id": timer["id"]}
        if comments is not None:
            body["comments"] = comments
        if billable is not None:
            body["notBillable"] = not billable
        self.client.put(f"timesheets/{timer['timesheet_id']}/timers/{timer['id']}", body,
                        source="TimeTrackersService.putUpdateTimer")

    # -- favourites -------------------------------------------------------

    def favourites(self) -> list[dict]:
        payload = self.client.get("favourite-timers", source="TimeTrackerFavoritesService.getFavorites")
        items, _ = _page_items(payload)
        return items

    def add_favourite(self, project_id: str | None, job_id: str) -> dict:
        body = {"id": new_id(), "projectId": project_id, "jobId": job_id}
        self.client.post("favourite-timers", body, source="TimeTrackerFavoritesService.addFavorite")
        fav = next((f for f in self.favourites() if f.get("id") == body["id"]), None)
        if fav is None:
            raise HidmaError(f"favourite was posted but {body['id']} is not in the list when read back")
        return fav

    def delete_favourite(self, fav_id: str) -> bool:
        self.client.delete(f"favourite-timers/{fav_id}", source="TimeTrackerFavoritesService.deleteFavorite")
        return not any(f.get("id") == fav_id for f in self.favourites())

    # -- setup objects: clients, projects, jobs -----------------------------

    def client_full(self, client_id: str) -> dict:
        payload = self.client.get(f"clients/{client_id}", source="clientEffects.fetchClientBasicDetails$")
        items, _ = _page_items(payload)
        if items:
            return items[0]
        if isinstance(payload, dict) and payload.get("id"):
            return payload
        raise HidmaError(f"client {client_id} not found")

    def add_client(self, name: str, *, type_shortcode: str = "c", vat: str = "") -> dict:
        """POST the client form's body (chunk-JRE2KU2S clientForm). Returns the client read back from the list."""
        ctype = self.type_by_shortcode("clientTypes", type_shortcode)
        body = {"id": new_id(), "avatarColour": None, "name": name, "status": self.type_by_shortcode("clientStates", "a")["id"],
                "type": ctype["id"], "defaultTaxRate": None, "vatNumber": vat or "", "contacts": [], "addresses": [],
                "comments": [], "chargeOutRates": None}
        self.client.post("clients/", body, source="clientEffects.createClient$")
        return self._read_back_client(body["id"])

    def update_client(self, client_id: str, *, name: str | None = None, vat: str | None = None,
                      type_shortcode: str | None = None, state_shortcode: str | None = None) -> dict:
        current = self.client_full(client_id)
        body = client_put_body(current)
        if name is not None:
            body["name"] = name
        if vat is not None:
            body["vatNumber"] = vat
        if type_shortcode is not None:
            body["type"] = self.type_by_shortcode("clientTypes", type_shortcode)["id"]
        if state_shortcode is not None:
            body["status"] = self.type_by_shortcode("clientStates", state_shortcode)["id"]
        self.client.put(f"clients/{client_id}", body, source="clientEffects.updateClient$")
        return self._read_back_client(client_id)

    def _read_back_client(self, client_id: str) -> dict:
        client = next((c for c in self.clients() if c.get("id") == client_id), None)
        if client is None:
            raise HidmaError(f"the write returned OK but client {client_id} is not in the client list when read back")
        return client

    def project_full(self, project_id: str) -> dict:
        """The project with jobs, client, team and status (projects/{id} only accepts PUT; the app reads stats/)."""
        payload = self.client.get(f"projects/stats/{project_id}/", source="projectsEffects.fetchProjectStats$")
        items, _ = _page_items(payload)
        if items:
            return items[0]
        raise HidmaError(f"project {project_id} not found")

    def add_project(self, name: str, *, client_id: str | None, job_ids: list[str], team_id: str,
                    type_shortcode: str = "b", description: str = "") -> dict:
        """POST the project form's body (chunk-MZ74N55M loadProjectFromForm, single create)."""
        ptype = self.type_by_shortcode("projectTypes", type_shortcode)
        if ptype["shortcode"] == "b" and not client_id:
            raise HidmaError("a business project needs a client")
        body = {"id": new_id(), "name": name, "description": description or None, "type": ptype["id"],
                "jobs": list(job_ids), "status": self.type_by_shortcode("projectStates", "a")["id"], "team": team_id,
                "budgets": [], "targetRecoverability": None, "startDate": None, "budgetNotificationsEnabled": True,
                "budgetNotifications": [], "comments": [], "template": None,
                "client": client_id if ptype["shortcode"] == "b" else None}
        self.client.post("projects/", body, source="projectsEffects.createProject$")
        return self.project_full(body["id"])

    def update_project(self, project_id: str, *, name: str | None = None, add_job_ids: list[str] = (),
                       remove_job_ids: list[str] = (), state_shortcode: str | None = None,
                       description: str | None = None) -> dict:
        current = self.project_full(project_id)
        body = project_put_body(current)
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        jobs = [j for j in body["jobs"] if j not in set(remove_job_ids)]
        jobs += [j for j in add_job_ids if j not in jobs]
        body["jobs"] = jobs
        if state_shortcode is not None:
            body["status"] = self.type_by_shortcode("projectStates", state_shortcode)["id"]
        self.client.put(f"projects/{project_id}", body, source="projectsEffects.updateProject$")
        return self.project_full(project_id)

    def job_full(self, job_id: str) -> dict:
        payload = self.client.get(f"jobs/{job_id}/", source="jobsEffects.fetchJob$")
        items, _ = _page_items(payload)
        if items:
            return items[0]
        raise HidmaError(f"job {job_id} not found")

    def add_job(self, name: str, *, type_shortcode: str = "b", description: str = "") -> dict:
        """POST the job form's body (chunk-BELRKIDE loadJobFromForm)."""
        jtype = self.type_by_shortcode("jobTypes", type_shortcode)
        body = {"id": new_id(), "name": name, "active": True, "type": jtype["id"], "description": description or "",
                "teams": None, "projects": None, "defaultTaxRate": 1, "defaultOrganisation": None, "chargeOutRates": None,
                "taxRates": 1, "comments": []}
        self.client.post("jobs/", body, source="jobsEffects.createJob$")
        return self.job_full(body["id"])

    def update_job(self, job_id: str, *, name: str | None = None, type_shortcode: str | None = None,
                   active: bool | None = None, description: str | None = None) -> dict:
        current = self.job_full(job_id)
        body = job_put_body(current)
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if type_shortcode is not None:
            body["type"] = self.type_by_shortcode("jobTypes", type_shortcode)["id"]
        if active is not None:
            body["active"] = active
        self.client.put(f"jobs/{job_id}", body, source="jobsEffects.updateJob$")
        return self.job_full(job_id)

    # -- reporting (read-only) ----------------------------------------------

    def raw_report(self, report_type: str = "unbilled", start: date = REPORT_EPOCH, end: date | None = None,
                   client_ids: list[str] | None = None, project_ids: list[str] | None = None) -> dict:
        """The Reports page's table (reports/raw/, 0-based page): one row per project and job with minutes and cost."""
        if report_type not in REPORT_TYPES:
            raise HidmaError(f"report type must be one of {', '.join(REPORT_TYPES)}")
        reporting = {"dateRange": {"start": iso_date(start), "end": iso_date(end or date.today())}}
        if client_ids:
            reporting["clients"] = list(client_ids)
        if project_ids:
            reporting["projects"] = list(project_ids)
        rows, totals, page = [], {}, 0
        while page < MAX_PAGES:
            payload = self.client.get("reports/raw/", {"page": page, "size": PAGE_SIZE, "sortOrder": "asc", "sortKey": "project",
                                                       "type": report_type, "billed": "false", "filters": {"reporting": reporting}},
                                      source="reports.service.rawReportPage")
            items, page_data = _page_items(payload)
            if isinstance(payload, dict) and isinstance(payload.get("totals"), dict):
                totals = payload["totals"]
            rows.extend(items)
            last = page_data.get("lastPage")
            if not items or last is None or page >= int(last):
                break
            page += 1
        return {"rows": rows, "totals": totals}

    def recent_entries(self, count: int, weeks_back: int = 12) -> list[dict]:
        """The last `count` cells by date, walking back week by week from today."""
        out: list[dict] = []
        monday = monday_of(date.today())
        for _ in range(weeks_back + 1):
            ts = self.week_timesheet(monday)
            if ts:
                out = sorted(flatten(ts), key=lambda e: e["date"]) + out
            if len(out) >= count:
                break
            monday -= timedelta(days=7)
        return out[-count:]


def local_stamp(moment: datetime) -> str:
    """Local wall-clock time without zone, the way the timer widget stamps events (chunk-M55H2YJN `gs`)."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def timer_state(timer: dict) -> str:
    events = timer.get("timerEvents") or []
    return str((events[-1] if events else {}).get("eventType") or "")


def timer_cell_id(timer: dict) -> str | None:
    if timer.get("timesheetRowCellId"):
        return timer["timesheetRowCellId"]
    cell = timer.get("timesheetRowCell")
    if isinstance(cell, dict) and cell.get("id"):
        return cell["id"]
    row = timer.get("timesheetRow")
    if isinstance(row, dict) and row.get("cells"):
        return (row["cells"][0] or {}).get("id")
    return None


def timer_started_at(timer: dict) -> datetime | None:
    """When the timer was started, as local wall-clock time; None when it has no events."""
    events = timer.get("timerEvents") or []
    if not events:
        return None
    at = _event_utc(events[0], stamp_skew(events))
    return at.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None) if at else None


def timer_day(timer: dict) -> date:
    started = timer_started_at(timer)
    return started.date() if started else date.today()


def _parse_stamp(value) -> datetime | None:
    """A stamp as naive UTC. '...Z' is what the API returns; a stamp without zone is one this process just made in
    local time (the app does the same, chunk-M55H2YJN `gs`)."""
    if not value:
        return None
    text = str(value)
    stored = text.endswith("Z")
    text = text.rstrip("Z").split(".")[0].replace(" ", "T")
    try:
        parsed = datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    if stored:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def stamp_skew(events: list[dict]) -> timedelta:
    """How far the stored event stamps sit from real time.

    The app stamps events with the browser's wall-clock time and no zone; the API reads that as the tenant's local
    time and stores UTC. When the browser is in another zone every stored stamp is off by the difference, so a
    running timer looks an hour long the moment it starts. Each event also carries a server-set createdAt, which is
    real UTC and at most a few seconds after the stamp, so the difference, rounded to the quarter hour, is the skew.
    """
    for ev in events:
        at, created = _parse_stamp(ev.get("startTime")), _parse_stamp(ev.get("createdAt"))
        if at and created and str(ev.get("startTime", "")).endswith("Z") and str(ev.get("createdAt", "")).endswith("Z"):
            quarter = 15 * 60
            return timedelta(seconds=round((created - at).total_seconds() / quarter) * quarter)
    return timedelta(0)


def _event_utc(ev: dict, skew: timedelta) -> datetime | None:
    at = _parse_stamp(ev.get("startTime"))
    if at is None:
        return None
    return at + skew if str(ev.get("startTime", "")).endswith("Z") else at


def accumulated_seconds(events: list[dict], now: datetime | None = None) -> int:
    """Seconds the timer ran, the app's way (chunk-M55H2YJN `hs`): START/RESUME to the next PAUSE/STOP, plus a
    still-running tail up to now (a naive UTC datetime; default the current time)."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    skew = stamp_skew(events)
    total, prev_type, prev_at = 0, "", None
    for i, ev in enumerate(events):
        kind = str(ev.get("eventType") or "")
        at = _event_utc(ev, skew)
        if kind in (TIMER_PAUSE, TIMER_STOP) and prev_type in (TIMER_START, TIMER_RESUME) and at and prev_at:
            total += int((at - prev_at).total_seconds())
        elif i == len(events) - 1 and kind in (TIMER_START, TIMER_RESUME) and at:
            total += int((now - at).total_seconds())
        prev_type, prev_at = kind, at
    return max(total, 0)


def round_seconds(seconds: int, rounding: dict | None) -> int:
    """Apply the tenant's timers.rounding ({enabled, minutes, direction}) like chunk-M55H2YJN `Ge`."""
    if not rounding or not rounding.get("enabled") or not rounding.get("minutes"):
        return seconds
    step = int(rounding["minutes"]) * 60
    if step <= 0:
        return seconds
    blocks = seconds // step if rounding.get("direction") == "down" else -(-seconds // step)
    return blocks * step


def _id_of(value):
    return value.get("id") if isinstance(value, dict) else value


def client_put_body(client: dict) -> dict:
    """The client as the edit form PUTs it: ids for type/status, nested lists kept."""
    return {"id": client["id"], "avatarColour": client.get("avatarColour"), "name": client.get("name"),
            "status": _id_of(client.get("status")), "type": _id_of(client.get("type")),
            "defaultTaxRate": _id_of(client.get("defaultTaxRate")), "vatNumber": client.get("vatNumber") or "",
            "contacts": client.get("contacts") or [], "addresses": client.get("addresses") or [], "comments": [],
            "chargeOutRates": None}


def project_put_body(project: dict) -> dict:
    """The project as the edit form PUTs it (loadProjectFromForm), built from projects/stats/{id}/."""
    return {"id": project["id"], "name": project.get("name"), "description": project.get("description"),
            "type": _id_of(project.get("type")), "team": _id_of(project.get("team")), "status": _id_of(project.get("status")),
            "billingType": project.get("billingTypeId") or _id_of(project.get("billingType")),
            "jobs": [_id_of(j) for j in project.get("jobs") or []], "client": _id_of(project.get("client")),
            "budgets": project.get("budgets") or [], "targetRecoverability": project.get("targetRecoverability"),
            "startDate": project.get("startDate"),
            "budgetNotificationsEnabled": project.get("budgetNotificationsEnabled", True),
            "budgetNotifications": project.get("budgetNotifications") or [], "comments": [], "template": None}


def job_put_body(job: dict) -> dict:
    teams = [_id_of(t) for t in job.get("teams") or []]
    return {"id": job["id"], "name": job.get("name"), "active": bool(job.get("active", True)), "type": _id_of(job.get("type")),
            "description": job.get("description") or "", "teams": teams or None,
            "defaultTaxRate": _id_of(job.get("defaultTaxRate")), "defaultOrganisation": _id_of(job.get("defaultOrganisation")),
            "taxRates": _id_of(job.get("defaultTaxRate")), "chargeOutRates": None}


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

