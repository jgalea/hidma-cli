"""Minimal stdio MCP server over the same calls the CLI makes. Run it with `hidma-mcp`.

Tools: whoami, projects, entries, today, week, log, report, unbilled, timer_start, timer_status, timer_stop.
Nothing here is registered anywhere; see the README.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import api as api_mod
from . import report as report_mod
from . import session as session_mod
from .client import HidmaError, SessionExpired
from .duration import parse_minutes
from .match import MatchError, match_one

mcp = MCPServer("hidma")


def _hidma() -> api_mod.Hidma:
    try:
        return api_mod.Hidma()
    except session_mod.NotLoggedIn as exc:
        raise ToolError(f"{exc}; run `hidma login` in a terminal")


def _guard(fn):
    try:
        return fn()
    except (session_mod.NotLoggedIn, SessionExpired) as exc:
        raise ToolError(f"hidma session expired ({exc}); run `hidma login` in a terminal")
    except (HidmaError, MatchError, ValueError) as exc:
        raise ToolError(str(exc))


def _day(text: str | None) -> date:
    if not text:
        return date.today()
    return datetime.strptime(text, "%Y-%m-%d").date()


def _public(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k != "cell"}


def _entries(h: api_mod.Hidma, start: date, end: date) -> dict:
    rows = h.entries(start, end)
    return {"from": str(start), "to": str(end), "entries": [_public(r) for r in rows], "totals": api_mod.totals(rows)}


@mcp.tool()
def whoami() -> dict:
    """The hidma user the saved session belongs to, and how old the session is."""
    def run():
        h = _hidma()
        me = h.me()
        return {"user_id": h.user_id, "tenant_id": h.client.tenant_id, "email": me.get("email"),
                "name": " ".join(x for x in (me.get("name"), me.get("surname")) if x),
                **session_mod.describe(h.client.data)}
    return _guard(run)


@mcp.tool()
def projects(client: str | None = None) -> list[dict]:
    """hidma projects with their client. client: optional client name substring."""
    def run():
        h = _hidma()
        client_id = match_one(h.clients(), client, "client")["id"] if client else None
        out = []
        for p in h.projects(client_id):
            c = p.get("client") if isinstance(p.get("client"), dict) else {}
            out.append({"id": p.get("id"), "code": p.get("code"), "name": p.get("name"), "client": c.get("name")})
        return out
    return _guard(run)


@mcp.tool()
def entries(start: str | None = None, end: str | None = None) -> dict:
    """Time entries between start and end (YYYY-MM-DD, default this week) with totals per day and project."""
    def run():
        h = _hidma()
        s = _day(start) if start else api_mod.monday_of(date.today())
        e = _day(end) if end else s + timedelta(days=6)
        return _entries(h, s, e)
    return _guard(run)


@mcp.tool()
def today() -> dict:
    """Today's time entries with totals."""
    return _guard(lambda: _entries(_hidma(), date.today(), date.today()))


@mcp.tool()
def week(day: str | None = None) -> dict:
    """The week containing `day` (YYYY-MM-DD, default today) as the app's grid: text plus the entries and totals."""
    def run():
        h = _hidma()
        monday = api_mod.monday_of(_day(day))
        ts = h.week_timesheet(monday)
        rows = api_mod.flatten(ts) if ts else []
        status = h.timesheet_state_name(ts) if ts else ""
        return {"week": str(monday), "status": status, "grid": report_mod.week_grid(rows, monday, status),
                "entries": [_public(r) for r in rows], "totals": api_mod.totals(rows)}
    return _guard(run)


@mcp.tool()
def log(duration: str, description: str, project: str, day: str | None = None, job: str | None = None,
        billable: bool = True) -> dict:
    """Log time on a project. duration: 1h30 / 90m / 1.5h. project: name substring, code or id. day: YYYY-MM-DD (default today)."""
    def run():
        from .cli import _resolve_target
        h = _hidma()
        proj, jb = _resolve_target(h, project, job)
        result = h.log(project=proj, job=jb, day=_day(day), minutes=parse_minutes(duration), comments=description,
                       billable=billable)
        return _public(result)
    return _guard(run)


@mcp.tool()
def report(by: str = "project", start: str | None = None, end: str | None = None) -> dict:
    """Minutes per client, project, job, day or week between start and end (YYYY-MM-DD, default this week)."""
    def run():
        h = _hidma()
        s = _day(start) if start else api_mod.monday_of(date.today())
        e = _day(end) if end else s + timedelta(days=6)
        return {"from": str(s), "to": str(e), "by": by, "rows": report_mod.aggregate(h.entries(s, e), by)}
    return _guard(run)


@mcp.tool()
def unbilled(client: str | None = None) -> dict:
    """hidma's unbilled report: time and cost not yet billed, per project and job. client: optional name substring."""
    def run():
        h = _hidma()
        client_ids = [match_one(h.clients(), client, "client")["id"]] if client else None
        data = h.raw_report("unbilled", client_ids=client_ids)
        rows = []
        for r in data["rows"]:
            p = r.get("project") if isinstance(r.get("project"), dict) else {}
            c = p.get("client") if isinstance(p.get("client"), dict) else {}
            j = r.get("job") if isinstance(r.get("job"), dict) else {}
            rows.append({"client": c.get("name"), "project": p.get("name"), "job": j.get("name"),
                         "minutes": r.get("minutes"), "cost": r.get("cost")})
        return {"rows": rows, "totals": data["totals"]}
    return _guard(run)


@mcp.tool()
def timer_start(description: str, project: str, job: str | None = None, billable: bool = True) -> dict:
    """Start a stopwatch timer on a project (hidma allows one at a time). project: name substring, code or id."""
    def run():
        from .cli import _resolve_target
        h = _hidma()
        proj, jb = _resolve_target(h, project, job)
        return h.start_timer(project=proj, job=jb, comments=description, billable=billable)
    return _guard(run)


@mcp.tool()
def timer_status() -> dict | None:
    """The running or paused timer with its state, elapsed seconds and the entry it feeds; null when there is none."""
    def run():
        h = _hidma()
        timer = h.active_timer()
        if timer is None:
            return None
        cell_id = api_mod.timer_cell_id(timer)
        entry = h.find_entry(cell_id, hint=api_mod.timer_day(timer), weeks_back=1) if cell_id else None
        return {"id": timer["id"], "state": api_mod.timer_state(timer).lower(),
                "elapsed_seconds": api_mod.accumulated_seconds(timer.get("timerEvents") or []),
                "comments": timer.get("comments"), "entry": _public(entry) if entry else None}
    return _guard(run)


@mcp.tool()
def timer_stop() -> dict:
    """Stop the running timer and write its minutes into today's entry."""
    def run():
        h = _hidma()
        timer = h.active_timer()
        if timer is None:
            raise ToolError("no running or paused timer")
        return h.stop_timer(timer)
    return _guard(run)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
