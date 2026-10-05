"""Minimal stdio MCP server over the same calls the CLI makes. Run it with `hidma-mcp`.

Tools: whoami, projects, entries, log. Nothing here is registered anywhere; see the README.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import api as api_mod
from . import session as session_mod
from .client import HidmaError, SessionExpired
from .duration import parse_minutes
from .match import MatchError

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


@mcp.tool()
def whoami() -> dict:
    """The hidma user the saved session belongs to, and how old the session is."""
    def run():
        h = _hidma()
        me = h.me()
        return {"user_id": h.user_id, "tenant_id": h.client.tenant_id, "email": me.get("email"),
                "name": " ".join(x for x in (me.get("firstName"), me.get("lastName")) if x),
                **session_mod.describe(h.client.data)}
    return _guard(run)


@mcp.tool()
def projects(client: str | None = None) -> list[dict]:
    """hidma projects with their client. client: optional client name substring."""
    def run():
        h = _hidma()
        from .match import match_one
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
        rows = h.entries(s, e)
        return {"from": str(s), "to": str(e), "entries": [{k: v for k, v in r.items() if k != "cell"} for r in rows],
                "totals": api_mod.totals(rows)}
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
        return {k: v for k, v in result.items() if k != "cell"}
    return _guard(run)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
