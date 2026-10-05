from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta

from . import __version__
from . import api as api_mod
from . import session as session_mod
from .client import HidmaError, SessionExpired
from .duration import format_minutes, parse_minutes
from .match import MatchError, match_one

EXIT_API = 1
EXIT_USAGE = 2
EXIT_SESSION = 4


def _emit(rows: list[dict], columns: list[tuple[str, str]], as_json: bool) -> None:
    if as_json:
        _emit_json(rows)
        return
    if not rows:
        print("no rows")
        return
    widths = [max(len(title), *(len(str(r.get(key, ""))) for r in rows)) for key, title in columns]
    print("  ".join(title.ljust(w) for (_, title), w in zip(columns, widths)))
    for row in rows:
        print("  ".join(str(row.get(key, "")).ljust(w) for (key, _), w in zip(columns, widths)))
    print(f"\n{len(rows)} row(s)")


def _emit_json(data) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")


def _parse_date(text: str | None) -> date:
    if not text or text == "today":
        return date.today()
    if text == "yesterday":
        return date.today() - timedelta(days=1)
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"date must be YYYY-MM-DD, got {text!r}")


def _public(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k != "cell"}


# -- commands -------------------------------------------------------------

def cmd_login(args) -> int:
    data = session_mod.login(args.wait * 60)
    print(f"session saved to {session_mod.SESSION_FILE} ({len(data['cookies'])} cookies).")
    return 0


def cmd_whoami(args) -> int:
    data = session_mod.load()
    h = api_mod.Hidma(api_mod.Client(data))
    me = h.me()
    info = {
        "user_id": h.user_id, "tenant_id": h.client.tenant_id,
        "name": " ".join(x for x in (me.get("firstName"), me.get("lastName")) if x) or me.get("name"),
        "email": me.get("email"),
    }
    info.update(session_mod.describe(h.client.data))
    if args.json:
        _emit_json(info)
    else:
        for key, value in info.items():
            print(f"{key + ':':<22}{value if value is not None else '-'}")
    return 0


def cmd_clients(args) -> int:
    rows = api_mod.Hidma().clients(args.filter)
    _emit(rows, [("id", "ID"), ("code", "CODE"), ("name", "NAME")], args.json)
    return 0


def _project_rows(projects: list[dict]) -> list[dict]:
    out = []
    for p in projects:
        client = p.get("client") if isinstance(p.get("client"), dict) else {}
        state = p.get("state") if isinstance(p.get("state"), dict) else {}
        out.append({"id": p.get("id"), "code": p.get("code", ""), "name": p.get("name", ""),
                    "client": client.get("name", ""), "state": state.get("name", p.get("state", "")),
                    "type": (p.get("type") or {}).get("name", "") if isinstance(p.get("type"), dict) else p.get("type", "")})
    return out


def cmd_projects(args) -> int:
    h = api_mod.Hidma()
    client_id = None
    if args.client:
        client_id = match_one(h.clients(), args.client, "client")["id"]
    rows = _project_rows(h.projects(client_id, args.filter))
    _emit(rows, [("id", "ID"), ("code", "CODE"), ("name", "NAME"), ("client", "CLIENT"), ("state", "STATE")], args.json)
    return 0


def cmd_jobs(args) -> int:
    rows = api_mod.Hidma().jobs(args.filter)
    out = [{"id": j.get("id"), "name": j.get("name", ""),
            "type": (j.get("type") or {}).get("name", "") if isinstance(j.get("type"), dict) else j.get("type", ""),
            "active": j.get("active", "")} for j in rows]
    _emit(out, [("id", "ID"), ("name", "NAME"), ("type", "TYPE"), ("active", "ACTIVE")], args.json)
    return 0


def _resolve_target(h: api_mod.Hidma, project_query: str | None, job_query: str | None) -> tuple[dict | None, dict]:
    """(project, job) for a time entry. A business job needs a project; the job defaults to the only business job."""
    jobs = h.jobs()
    project = None
    if project_query:
        project = match_one(h.projects(), project_query, "project", names=("name", "code"))
    if job_query:
        job = match_one(jobs, job_query, "job")
    else:
        business = [j for j in jobs if _job_shortcode(j) == "b" and j.get("active", True)]
        if project is None:
            raise MatchError("give --project (business time) or --job (internal/personal time)")
        if len(business) != 1:
            names = ", ".join(j.get("name", "") for j in business) or "none"
            raise MatchError(f"pick a job with --job; business jobs: {names}")
        job = business[0]
    if _job_shortcode(job) == "b" and project is None:
        raise MatchError(f"job {job.get('name')!r} needs a project; add --project")
    return project, job


def _job_shortcode(job: dict) -> str:
    t = job.get("type")
    if isinstance(t, dict):
        return str(t.get("shortcode", "")).lower()
    return ""


def cmd_log(args) -> int:
    minutes = parse_minutes(args.duration)
    h = api_mod.Hidma()
    project, job = _resolve_target(h, args.project, args.job)
    result = h.log(project=project, job=job, day=args.date, minutes=minutes, comments=args.description,
                   billable=args.billable)
    if args.json:
        _emit_json(_public(result))
        return 0
    label = " / ".join(x for x in (result["client"], result["project"], result["job"]) if x)
    verb = {"new-row": "logged", "new-cell": "logged", "merged": "merged into the day's existing entry, now"}[result["action"]]
    print(f"{verb} {format_minutes(result['minutes'])} on {result['date']} for {label}")
    print(f"id: {result['id']}")
    if result["not_billable"]:
        print(f"not billable: {format_minutes(result['not_billable'])}")
    return 0


def _range(args) -> tuple[date, date]:
    if args.start or args.end:
        start = args.start or args.end
        end = args.end or args.start
        return start, end
    if args.week:
        start = api_mod.monday_of(args.week)
        return start, start + timedelta(days=6)
    start = api_mod.monday_of(date.today())
    return start, start + timedelta(days=6)


def cmd_entries(args) -> int:
    start, end = _range(args)
    h = api_mod.Hidma()
    entries = h.entries(start, end)
    if args.project:
        q = args.project.lower()
        entries = [e for e in entries if q in e["project"].lower() or q in e["client"].lower() or q == str(e["project_id"])]
    t = api_mod.totals(entries)
    if args.json:
        _emit_json({"from": str(start), "to": str(end), "entries": [_public(e) for e in entries], "totals": t})
        return 0
    rows = [dict(e, duration=format_minutes(e["minutes"]),
                 nb=format_minutes(e["not_billable"]) if e["not_billable"] else "") for e in entries]
    _emit(rows, [("date", "DATE"), ("client", "CLIENT"), ("project", "PROJECT"), ("job", "JOB"),
                 ("duration", "TIME"), ("nb", "NOT BILLABLE"), ("comments", "COMMENT"), ("id", "ID")], False)
    if entries:
        print("\nper day:     " + "  ".join(f"{d[5:]} {format_minutes(m)}" for d, m in t["by_day"].items()))
        print("per project: " + "  ".join(f"{k} {format_minutes(m)}" for k, m in t["by_project"].items()))
        print(f"total:       {format_minutes(t['total'])}  ({start} to {end})")
    return 0


def _locate(h: api_mod.Hidma, cell_id: str, hint: date | None) -> dict:
    entry = h.find_entry(cell_id, hint=hint)
    if entry is None:
        raise MatchError(f"no entry with id {cell_id} in the last 12 weeks (pass --date to look at another week)")
    return entry


def cmd_edit(args) -> int:
    if args.duration is None and args.description is None and args.billable is None:
        raise MatchError("nothing to change: give --duration, --description or --billable/--no-billable")
    h = api_mod.Hidma()
    entry = _locate(h, args.id, args.date)
    minutes = parse_minutes(args.duration) if args.duration else None
    result = h.update_cell(entry, minutes=minutes, comments=args.description, billable=args.billable)
    if args.json:
        _emit_json(_public(result))
        return 0
    label = " / ".join(x for x in (result["client"], result["project"], result["job"]) if x)
    print(f"updated {result['id']}: {format_minutes(result['minutes'])} on {result['date']} for {label}"
          + (f", not billable {format_minutes(result['not_billable'])}" if result["not_billable"] else ""))
    return 0


def cmd_delete(args) -> int:
    h = api_mod.Hidma()
    entry = _locate(h, args.id, args.date)
    label = " / ".join(x for x in (entry["client"], entry["project"], entry["job"]) if x)
    if not args.yes:
        print(f"would delete {format_minutes(entry['minutes'])} on {entry['date']} for {label} ({entry['id']}); "
              "re-run with --yes", file=sys.stderr)
        return EXIT_USAGE
    result = h.delete_cell(entry)
    if args.json:
        _emit_json(result)
        return 0
    if not result["gone"]:
        print(f"hidma: {entry['id']} is still present after the delete call", file=sys.stderr)
        return EXIT_API
    print(f"deleted {format_minutes(entry['minutes'])} on {entry['date']} for {label}"
          + (" (its row had no other entries, removed too)" if result["row_deleted"] else ""))
    return 0


def cmd_timer(args) -> int:
    h = api_mod.Hidma()
    data = h.timers_for_week(args.date)
    timers = data.get("timers") or []
    if args.json:
        _emit_json(timers)
        return 0
    rows = []
    for t in timers:
        events = t.get("timerEvents") or []
        last = events[-1] if events else {}
        rows.append({"id": t.get("id"), "cell": t.get("timesheetRowCellId") or (t.get("timesheetRowCell") or {}).get("id"),
                     "state": last.get("eventType", ""), "since": last.get("startTime", ""), "comments": t.get("comments", "")})
    _emit(rows, [("id", "ID"), ("cell", "CELL"), ("state", "LAST EVENT"), ("since", "AT"), ("comments", "COMMENT")], False)
    return 0


# -- parser ---------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="emit JSON instead of a table")

    parser = argparse.ArgumentParser(prog="hidma", description="Log and read time in hidma from the terminal.")
    parser.add_argument("--version", action="version", version=f"hidma {__version__}")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    sub = parser.add_subparsers(dest="command", required=True)

    lg = sub.add_parser("login", help="open a browser window to sign in (one-time; the session is then refreshed headlessly)")
    lg.add_argument("--wait", type=int, default=5, help="minutes to wait for the sign-in (default 5)")
    lg.set_defaults(func=cmd_login)

    sub.add_parser("whoami", parents=[common], help="who the session belongs to and how old it is").set_defaults(func=cmd_whoami)

    c = sub.add_parser("clients", parents=[common], help="list clients")
    c.add_argument("--filter", help="server-side name filter")
    c.set_defaults(func=cmd_clients)

    p = sub.add_parser("projects", parents=[common], help="list projects")
    p.add_argument("--client", help="only this client (name substring or id)")
    p.add_argument("--filter", help="server-side name filter")
    p.set_defaults(func=cmd_projects)

    j = sub.add_parser("jobs", parents=[common], help="list jobs (the activity types a time entry needs)")
    j.add_argument("--filter", help="server-side name filter")
    j.set_defaults(func=cmd_jobs)

    lo = sub.add_parser("log", parents=[common], help="log time: hidma log 1h30 \"what you did\" --project X")
    lo.add_argument("duration", help="1h30, 90m, 1.5h or 1:30")
    lo.add_argument("description", help="the entry comment")
    lo.add_argument("--project", help="project name substring, code or id")
    lo.add_argument("--job", help="job name or id (defaults to the only business job when --project is given)")
    lo.add_argument("--date", type=_parse_date, default=date.today(), help="YYYY-MM-DD, today or yesterday (default today)")
    lo.add_argument("--billable", dest="billable", action="store_true", default=True)
    lo.add_argument("--no-billable", dest="billable", action="store_false", help="mark the whole entry as not billable")
    lo.set_defaults(func=cmd_log)

    en = sub.add_parser("entries", parents=[common], help="list entries with totals per day and project (default: this week)")
    en.add_argument("--week", type=_parse_date, help="the week containing this date")
    en.add_argument("--from", dest="start", type=_parse_date, help="YYYY-MM-DD")
    en.add_argument("--to", dest="end", type=_parse_date, help="YYYY-MM-DD")
    en.add_argument("--project", help="only entries whose project or client contains this")
    en.set_defaults(func=cmd_entries)

    ed = sub.add_parser("edit", parents=[common], help="change an entry's duration, comment or billable flag")
    ed.add_argument("id", help="the entry id (ID column of `entries`)")
    ed.add_argument("--duration", help="new duration")
    ed.add_argument("--description", help="new comment")
    ed.add_argument("--billable", dest="billable", action="store_true", default=None)
    ed.add_argument("--no-billable", dest="billable", action="store_false")
    ed.add_argument("--date", type=_parse_date, help="the week to look in first")
    ed.set_defaults(func=cmd_edit)

    de = sub.add_parser("delete", parents=[common], help="delete an entry (needs --yes)")
    de.add_argument("id", help="the entry id")
    de.add_argument("--yes", action="store_true", help="actually delete")
    de.add_argument("--date", type=_parse_date, help="the week to look in first")
    de.set_defaults(func=cmd_delete)

    ti = sub.add_parser("timers", parents=[common], help="list the week's stopwatch timers (read-only)")
    ti.add_argument("--date", type=_parse_date, default=date.today(), help="the week containing this date")
    ti.set_defaults(func=cmd_timer)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except session_mod.NotLoggedIn as exc:
        print(f"hidma: {exc}", file=sys.stderr)
        return EXIT_SESSION
    except SessionExpired as exc:
        print(f"hidma: {exc}", file=sys.stderr)
        return EXIT_SESSION
    except (MatchError, ValueError) as exc:
        print(f"hidma: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except HidmaError as exc:
        print(f"hidma: {exc}", file=sys.stderr)
        return EXIT_API


if __name__ == "__main__":
    raise SystemExit(main())
