from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta

from . import __version__
from . import api as api_mod
from . import report as report_mod
from . import session as session_mod
from .client import HidmaError, SessionExpired
from .duration import format_minutes, parse_minutes
from .match import MatchError, match_one

EXIT_API = 1
EXIT_USAGE = 2
EXIT_SESSION = 4

ENTRY_COLUMNS = [("date", "DATE"), ("client", "CLIENT"), ("project", "PROJECT"), ("job", "JOB"), ("duration", "TIME"),
                 ("nb", "NOT BILLABLE"), ("comments", "COMMENT"), ("id", "ID")]


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


def _parse_month(text: str) -> tuple[date, date]:
    try:
        first = datetime.strptime(text, "%Y-%m").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"month must be YYYY-MM, got {text!r}")
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, nxt - timedelta(days=1)


def _public(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k != "cell"}


def _label(entry: dict) -> str:
    return " / ".join(x for x in (entry["client"], entry["project"], entry["job"]) if x)


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
        "name": " ".join(x for x in (me.get("name"), me.get("surname")) if x),
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


def _name_of(value) -> str:
    return value.get("name", "") if isinstance(value, dict) else ("" if value is None else str(value))


def _project_rows(projects: list[dict]) -> list[dict]:
    return [{"id": p.get("id"), "code": p.get("code", ""), "name": p.get("name", ""), "client": _name_of(p.get("client")),
             "state": _name_of(p.get("state") or p.get("status")), "type": _name_of(p.get("type"))} for p in projects]


def cmd_projects(args) -> int:
    h = api_mod.Hidma()
    client_id = None
    if args.client:
        client_id = match_one(h.clients(), args.client, "client")["id"]
    rows = _project_rows(h.projects(client_id, args.filter))
    _emit(rows, [("id", "ID"), ("code", "CODE"), ("name", "NAME"), ("client", "CLIENT"), ("state", "STATE")], args.json)
    return 0


def cmd_jobs(args) -> int:
    h = api_mod.Hidma()
    out = [{"id": j.get("id"), "name": j.get("name", ""), "type": h.job_shortcode(j), "active": j.get("active", "")}
           for j in h.jobs(args.filter)]
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
        business = [j for j in jobs if h.job_shortcode(j) == "b" and j.get("active", True)]
        if project is None:
            raise MatchError("give --project (business time) or --job (internal/personal time)")
        if len(business) != 1:
            names = ", ".join(j.get("name", "") for j in business) or "none"
            raise MatchError(f"pick a job with --job; business jobs: {names}")
        job = business[0]
    if h.job_shortcode(job) == "b" and project is None:
        raise MatchError(f"job {job.get('name')!r} needs a project; add --project")
    return project, job


def cmd_log(args) -> int:
    minutes = parse_minutes(args.duration)
    h = api_mod.Hidma()
    project, job = _resolve_target(h, args.project, args.job)
    result = h.log(project=project, job=job, day=args.date, minutes=minutes, comments=args.description,
                   billable=args.billable)
    if args.json:
        _emit_json(_public(result))
        return 0
    verb = {"new-row": "logged", "new-cell": "logged", "merged": "merged into the day's existing entry, now"}[result["action"]]
    print(f"{verb} {format_minutes(result['minutes'])} on {result['date']} for {_label(result)}")
    print(f"id: {result['id']}")
    if result["not_billable"]:
        print(f"not billable: {format_minutes(result['not_billable'])}")
    return 0


def _range(args) -> tuple[date, date]:
    if args.start or args.end:
        start = args.start or args.end
        end = args.end or args.start
        return start, end
    if getattr(args, "month", None):
        return args.month
    if args.week:
        start = api_mod.monday_of(args.week)
        return start, start + timedelta(days=6)
    start = api_mod.monday_of(date.today())
    return start, start + timedelta(days=6)


def _print_entries(entries: list[dict], start: date, end: date) -> None:
    t = api_mod.totals(entries)
    rows = [dict(e, duration=format_minutes(e["minutes"]),
                 nb=format_minutes(e["not_billable"]) if e["not_billable"] else "") for e in entries]
    _emit(rows, ENTRY_COLUMNS, False)
    if entries:
        print("\nper day:     " + "  ".join(f"{d[5:]} {format_minutes(m)}" for d, m in t["by_day"].items()))
        print("per project: " + "  ".join(f"{k} {format_minutes(m)}" for k, m in t["by_project"].items()))
        print(f"total:       {format_minutes(t['total'])}  ({start} to {end})")


def cmd_entries(args) -> int:
    start, end = _range(args)
    h = api_mod.Hidma()
    entries = h.entries(start, end)
    if args.project:
        q = args.project.lower()
        entries = [e for e in entries if q in e["project"].lower() or q in e["client"].lower() or q == str(e["project_id"])]
    if args.json:
        _emit_json({"from": str(start), "to": str(end), "entries": [_public(e) for e in entries], "totals": api_mod.totals(entries)})
        return 0
    _print_entries(entries, start, end)
    return 0


def cmd_today(args) -> int:
    args.start = args.end = date.today()
    args.week = args.project = None
    return cmd_entries(args)


def cmd_last(args) -> int:
    entries = api_mod.Hidma().recent_entries(args.count)
    if args.json:
        _emit_json([_public(e) for e in entries])
        return 0
    rows = [dict(e, duration=format_minutes(e["minutes"]),
                 nb=format_minutes(e["not_billable"]) if e["not_billable"] else "") for e in entries]
    _emit(rows, ENTRY_COLUMNS, False)
    return 0


def cmd_week(args) -> int:
    h = api_mod.Hidma()
    monday = api_mod.monday_of(args.date)
    ts = h.week_timesheet(monday)
    entries = api_mod.flatten(ts) if ts else []
    status = h.timesheet_state_name(ts) if ts else ""
    if args.json:
        _emit_json({"week": str(monday), "status": status, "entries": [_public(e) for e in entries], "totals": api_mod.totals(entries)})
        return 0
    print(report_mod.week_grid(entries, monday, status))
    return 0


def cmd_report(args) -> int:
    h = api_mod.Hidma()
    if args.unbilled:
        return _unbilled_report(h, args)
    start, end = _range(args)
    rows = report_mod.aggregate(h.entries(start, end), args.by)
    if args.json:
        _emit_json({"from": str(start), "to": str(end), "by": args.by, "rows": rows})
        return 0
    if args.csv:
        report_mod.write_csv(rows, ["key", "minutes", "billable", "not_billable", "entries"])
        return 0
    out = [dict(r, time=format_minutes(r["minutes"]), billable_time=format_minutes(r["billable"]),
                nb=format_minutes(r["not_billable"]) if r["not_billable"] else "") for r in rows]
    _emit(out, [("key", args.by.upper()), ("time", "TIME"), ("billable_time", "BILLABLE"), ("nb", "NOT BILLABLE"),
                ("entries", "ENTRIES")], False)
    if rows:
        print(f"total:       {format_minutes(sum(r['minutes'] for r in rows))}  ({start} to {end})")
    return 0


def _unbilled_report(h: api_mod.Hidma, args) -> int:
    client_ids = [match_one(h.clients(), args.client, "client")["id"]] if args.client else None
    start = args.start or api_mod.REPORT_EPOCH
    end = args.end or date.today()
    data = h.raw_report("unbilled", start, end, client_ids=client_ids)
    rows = []
    for r in data["rows"]:
        project = r.get("project") if isinstance(r.get("project"), dict) else {}
        rows.append({"client": _name_of(project.get("client")), "project": project.get("name", ""),
                     "job": _name_of(r.get("job")), "minutes": int(r.get("minutes") or 0), "cost": r.get("cost"),
                     "row_ids": r.get("rowIds") or []})
    if args.json:
        _emit_json({"from": str(start), "to": str(end), "rows": rows, "totals": data["totals"]})
        return 0
    if args.csv:
        report_mod.write_csv(rows, ["client", "project", "job", "minutes", "cost"])
        return 0
    out = [dict(r, time=format_minutes(r["minutes"]), amount=report_mod.money(r["cost"])) for r in rows]
    _emit(out, [("client", "CLIENT"), ("project", "PROJECT"), ("job", "JOB"), ("time", "UNBILLED"), ("amount", "COST")], False)
    totals = data["totals"]
    if rows:
        print(f"total:       {format_minutes(int(totals.get('totalMinutes') or 0))}  cost {report_mod.money(totals.get('totalCost'))}"
              f"  ({start} to {end})")
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
    print(f"updated {result['id']}: {format_minutes(result['minutes'])} on {result['date']} for {_label(result)}"
          + (f", not billable {format_minutes(result['not_billable'])}" if result["not_billable"] else ""))
    return 0


def cmd_delete(args) -> int:
    h = api_mod.Hidma()
    entry = _locate(h, args.id, args.date)
    if not args.yes:
        print(f"would delete {format_minutes(entry['minutes'])} on {entry['date']} for {_label(entry)} ({entry['id']}); "
              "re-run with --yes", file=sys.stderr)
        return EXIT_USAGE
    result = h.delete_cell(entry)
    if args.json:
        _emit_json(result)
        return 0
    if not result["gone"]:
        print(f"hidma: {entry['id']} is still present after the delete call", file=sys.stderr)
        return EXIT_API
    print(f"deleted {format_minutes(entry['minutes'])} on {entry['date']} for {_label(entry)}"
          + (" (its row had no other entries, removed too)" if result["row_deleted"] else ""))
    return 0


# -- timers ---------------------------------------------------------------

def _timer_rows(h: api_mod.Hidma, data: dict) -> list[dict]:
    ts = data.get("timesheet") or {}
    labels = {e["id"]: _label(e) for e in api_mod.flatten(h.timesheet(ts["id"]))} if ts.get("id") else {}
    rows = []
    for t in data.get("timers") or []:
        started = api_mod.timer_started_at(t)
        rows.append({"id": t.get("id"), "state": api_mod.timer_state(t).lower() or "-",
                     "elapsed": format_minutes(api_mod.accumulated_seconds(t.get("timerEvents") or []) // 60),
                     "since": started.strftime("%Y-%m-%d %H:%M") if started else "",
                     "target": labels.get(api_mod.timer_cell_id(t), ""), "comments": t.get("comments") or ""})
    return rows


TIMER_COLUMNS = [("state", "STATE"), ("elapsed", "ELAPSED"), ("since", "STARTED"), ("target", "ON"), ("comments", "COMMENT"), ("id", "ID")]


def cmd_timer_list(args) -> int:
    h = api_mod.Hidma()
    data = h.timers_for_week(args.date)
    if args.json:
        _emit_json(data.get("timers") or [])
        return 0
    _emit(_timer_rows(h, data), TIMER_COLUMNS, False)
    return 0


def cmd_timer_start(args) -> int:
    h = api_mod.Hidma()
    project, job = _resolve_target(h, args.project, args.job)
    result = h.start_timer(project=project, job=job, comments=args.description, billable=args.billable)
    if args.json:
        _emit_json(result)
        return 0
    target = " / ".join(x for x in (_name_of((project or {}).get("client")), (project or {}).get("name", ""), job.get("name", "")) if x)
    print(f"timer started on {target}" + (f" (today's entry already holds {format_minutes(result['base_minutes'])})"
                                            if result["base_minutes"] else ""))
    print(f"id: {result['timer']['id']}")
    return 0


def _running(h: api_mod.Hidma) -> dict:
    timer = h.active_timer()
    if timer is None:
        raise MatchError("no running or paused timer this week")
    return timer


def cmd_timer_status(args) -> int:
    h = api_mod.Hidma()
    timer = h.active_timer()
    if args.json:
        _emit_json(timer)
        return 0
    if timer is None:
        print("no running or paused timer")
        return 0
    row = _timer_rows(h, {"timesheet": {"id": timer["timesheet_id"]}, "timers": [timer]})[0]
    print(f"{row['state']} {row['elapsed']} on {row['target'] or '?'} since {row['since']}"
          + (f': "{row["comments"]}"' if row["comments"] else ""))
    return 0


def cmd_timer_pause(args) -> int:
    h = api_mod.Hidma()
    timer = _running(h)
    if api_mod.timer_state(timer) == api_mod.TIMER_PAUSE:
        raise MatchError("the timer is already paused")
    h.timer_event(timer, api_mod.TIMER_PAUSE)
    print(f"paused after {format_minutes(api_mod.accumulated_seconds(timer.get('timerEvents') or []) // 60)}")
    return 0


def cmd_timer_resume(args) -> int:
    h = api_mod.Hidma()
    timer = _running(h)
    if api_mod.timer_state(timer) != api_mod.TIMER_PAUSE:
        raise MatchError("the timer is not paused")
    h.timer_event(timer, api_mod.TIMER_RESUME)
    print("resumed")
    return 0


def cmd_timer_stop(args) -> int:
    h = api_mod.Hidma()
    result = h.stop_timer(_running(h))
    if args.json:
        _emit_json(result)
        return 0
    ran = result["seconds"]
    cell = result["cell"]
    print(f"stopped after {ran // 3600}h{ran % 3600 // 60:02d}m{ran % 60:02d}s")
    if cell:
        print(f"{_label(cell)} on {cell['date']}: now {format_minutes(cell['minutes'])} (id {cell['id']})"
              + (" (written by the CLI, the server left the entry untouched)" if result["cli_wrote_minutes"] else ""))
    else:
        print("hidma: the timer's entry could not be read back; check `hidma today`", file=sys.stderr)
        return EXIT_API
    return 0


def cmd_timer_discard(args) -> int:
    h = api_mod.Hidma()
    timer = _running(h)
    if not args.yes:
        print(f"would discard the {api_mod.timer_state(timer).lower()} timer {timer['id']} and its tracked time; "
              "re-run with --yes", file=sys.stderr)
        return EXIT_USAGE
    result = h.discard_timer(timer)
    if args.json:
        _emit_json(result)
        return 0
    if not result["gone"]:
        print(f"hidma: timer {timer['id']} is still present after the delete call", file=sys.stderr)
        return EXIT_API
    print("timer discarded" + (f"; its entry {result['cell_id']} is still there with "
                               f"{format_minutes(result['cell_minutes'])}" if result["cell_present"] else ""))
    return 0


# -- favourites -----------------------------------------------------------

def _fav_rows(favs: list[dict]) -> list[dict]:
    rows = []
    for f in favs:
        project = f.get("project") if isinstance(f.get("project"), dict) else {}
        rows.append({"id": f.get("id"), "client": _name_of(project.get("client")), "project": project.get("name", ""),
                     "job": _name_of(f.get("job"))})
    return rows


def cmd_fav_list(args) -> int:
    favs = api_mod.Hidma().favourites()
    if args.json:
        _emit_json(favs)
        return 0
    _emit(_fav_rows(favs), [("client", "CLIENT"), ("project", "PROJECT"), ("job", "JOB"), ("id", "ID")], False)
    return 0


def cmd_fav_add(args) -> int:
    h = api_mod.Hidma()
    project, job = _resolve_target(h, args.project, args.job)
    fav = h.add_favourite(project["id"] if project else None, job["id"])
    if args.json:
        _emit_json(fav)
        return 0
    row = _fav_rows([fav])[0]
    print(f"favourite added: {' / '.join(x for x in (row['client'], row['project'], row['job']) if x)} (id {row['id']})")
    return 0


def cmd_fav_remove(args) -> int:
    h = api_mod.Hidma()
    fav = next((f for f in h.favourites() if f.get("id") == args.id), None)
    if fav is None:
        raise MatchError(f"no favourite with id {args.id}")
    if not h.delete_favourite(args.id):
        print(f"hidma: favourite {args.id} is still present after the delete call", file=sys.stderr)
        return EXIT_API
    print("favourite removed")
    return 0


# -- clients, projects, jobs ----------------------------------------------

def cmd_client_add(args) -> int:
    client = api_mod.Hidma().add_client(args.name, type_shortcode=args.type, vat=args.vat or "")
    if args.json:
        _emit_json(client)
        return 0
    print(f"client created: {client.get('name')} (id {client.get('id')})")
    return 0


def cmd_client_edit(args) -> int:
    if args.name is None and args.vat is None and args.type is None and args.state is None:
        raise MatchError("nothing to change: give --name, --vat, --type or --state")
    h = api_mod.Hidma()
    client = match_one(h.clients(), args.client, "client")
    result = h.update_client(client["id"], name=args.name, vat=args.vat, type_shortcode=args.type, state_shortcode=args.state)
    if args.json:
        _emit_json(result)
        return 0
    print(f"client updated: {result.get('name')} (id {result.get('id')})")
    return 0


def _only_team(h: api_mod.Hidma, query: str | None) -> str:
    teams = h.teams()
    if query:
        return match_one(teams, query, "team")["id"]
    if len(teams) != 1:
        raise MatchError("pick a team with --team; teams: " + (", ".join(t.get("name", "") for t in teams) or "none"))
    return teams[0]["id"]


def cmd_project_add(args) -> int:
    h = api_mod.Hidma()
    jobs = h.jobs()
    job_ids = [match_one(jobs, q, "job")["id"] for q in args.jobs.split(",") if q.strip()]
    client_id = match_one(h.clients(), args.client, "client")["id"] if args.client else None
    project = h.add_project(args.name, client_id=client_id, job_ids=job_ids, team_id=_only_team(h, args.team),
                            type_shortcode="i" if args.internal else "b", description=args.description or "")
    if args.json:
        _emit_json(project)
        return 0
    print(f"project created: {project.get('name')} (id {project.get('id')})")
    return 0


def cmd_project_edit(args) -> int:
    if args.name is None and args.description is None and args.state is None and not args.add_job and not args.remove_job:
        raise MatchError("nothing to change: give --name, --description, --state, --add-job or --remove-job")
    h = api_mod.Hidma()
    project = match_one(h.projects(), args.project, "project", names=("name", "code"))
    jobs = h.jobs()
    add = [match_one(jobs, q, "job")["id"] for q in args.add_job]
    remove = [match_one(jobs, q, "job")["id"] for q in args.remove_job]
    result = h.update_project(project["id"], name=args.name, add_job_ids=add, remove_job_ids=remove,
                              state_shortcode=args.state, description=args.description)
    if args.json:
        _emit_json(result)
        return 0
    print(f"project updated: {result.get('name')} (id {result.get('id')}), jobs: "
          + ", ".join(_name_of(j) for j in result.get("jobs") or []))
    return 0


def cmd_job_add(args) -> int:
    job = api_mod.Hidma().add_job(args.name, type_shortcode=args.type, description=args.description or "")
    if args.json:
        _emit_json(job)
        return 0
    print(f"job created: {job.get('name')} (id {job.get('id')})")
    return 0


def cmd_job_edit(args) -> int:
    if args.name is None and args.description is None and args.type is None and args.active is None:
        raise MatchError("nothing to change: give --name, --description, --type, --active or --inactive")
    h = api_mod.Hidma()
    job = match_one(h.jobs(), args.job, "job")
    result = h.update_job(job["id"], name=args.name, type_shortcode=args.type, active=args.active, description=args.description)
    if args.json:
        _emit_json(result)
        return 0
    print(f"job updated: {result.get('name')} (id {result.get('id')}), active: {result.get('active')}")
    return 0


def cmd_teams(args) -> int:
    rows = api_mod.Hidma().teams()
    _emit(rows, [("id", "ID"), ("name", "NAME")], args.json)
    return 0


def cmd_users(args) -> int:
    h = api_mod.Hidma()
    teams = {t.get("id"): t.get("name", "") for t in h.teams()}
    rows = [{"id": u.get("id"), "name": " ".join(x for x in (u.get("name"), u.get("surname")) if x),
             "team": teams.get(u.get("teamId"), u.get("teamId") or ""), "active": u.get("active", "")} for u in h.users()]
    _emit(rows, [("id", "ID"), ("name", "NAME"), ("team", "TEAM"), ("active", "ACTIVE")], args.json)
    return 0


# -- parser ---------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="emit JSON instead of a table")

    ranged = argparse.ArgumentParser(add_help=False)
    ranged.add_argument("--week", type=_parse_date, help="the week containing this date")
    ranged.add_argument("--month", type=_parse_month, help="YYYY-MM")
    ranged.add_argument("--from", dest="start", type=_parse_date, help="YYYY-MM-DD")
    ranged.add_argument("--to", dest="end", type=_parse_date, help="YYYY-MM-DD")

    target = argparse.ArgumentParser(add_help=False)
    target.add_argument("--project", help="project name substring, code or id")
    target.add_argument("--job", help="job name or id (defaults to the only business job when --project is given)")

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

    lo = sub.add_parser("log", parents=[common, target], help="log time: hidma log 1h30 \"what you did\" --project X")
    lo.add_argument("duration", help="1h30, 90m, 1.5h or 1:30")
    lo.add_argument("description", help="the entry comment")
    lo.add_argument("--date", type=_parse_date, default=date.today(), help="YYYY-MM-DD, today or yesterday (default today)")
    lo.add_argument("--billable", dest="billable", action="store_true", default=True)
    lo.add_argument("--no-billable", dest="billable", action="store_false", help="mark the whole entry as not billable")
    lo.set_defaults(func=cmd_log)

    en = sub.add_parser("entries", parents=[common, ranged], help="list entries with totals per day and project (default: this week)")
    en.add_argument("--project", help="only entries whose project or client contains this")
    en.set_defaults(func=cmd_entries)

    sub.add_parser("today", parents=[common], help="today's entries").set_defaults(func=cmd_today)

    la = sub.add_parser("last", parents=[common], help="the most recent entries")
    la.add_argument("count", nargs="?", type=int, default=10, help="how many (default 10)")
    la.set_defaults(func=cmd_last)

    wk = sub.add_parser("week", parents=[common], help="the week as a grid: one row per project and job, a column per day")
    wk.add_argument("--date", type=_parse_date, default=date.today(), help="the week containing this date")
    wk.set_defaults(func=cmd_week)

    rp = sub.add_parser("report", parents=[common, ranged], help="time per client, project, job, day or week; or unbilled time")
    rp.add_argument("--by", choices=report_mod.GROUPS, default="project")
    rp.add_argument("--unbilled", action="store_true", help="hidma's unbilled report instead: time and cost not yet billed")
    rp.add_argument("--client", help="unbilled: only this client")
    rp.add_argument("--csv", action="store_true", help="CSV instead of a table")
    rp.set_defaults(func=cmd_report)

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

    ti = sub.add_parser("timer", help="stopwatch timers: start, status, pause, resume, stop, discard, list")
    tsub = ti.add_subparsers(dest="action", required=True)
    ts = tsub.add_parser("start", parents=[common, target], help="start a timer on a project (one at a time)")
    ts.add_argument("description", nargs="?", default="", help="the timer comment")
    ts.add_argument("--billable", dest="billable", action="store_true", default=True)
    ts.add_argument("--no-billable", dest="billable", action="store_false")
    ts.set_defaults(func=cmd_timer_start)
    tsub.add_parser("status", parents=[common], help="the running or paused timer").set_defaults(func=cmd_timer_status)
    tsub.add_parser("pause", parents=[common]).set_defaults(func=cmd_timer_pause)
    tsub.add_parser("resume", parents=[common]).set_defaults(func=cmd_timer_resume)
    tsub.add_parser("stop", parents=[common], help="stop and write the minutes into today's entry").set_defaults(func=cmd_timer_stop)
    td = tsub.add_parser("discard", parents=[common], help="drop the timer and its tracked time (needs --yes)")
    td.add_argument("--yes", action="store_true")
    td.set_defaults(func=cmd_timer_discard)
    tl = tsub.add_parser("list", parents=[common], help="the week's timers")
    tl.add_argument("--date", type=_parse_date, default=date.today(), help="the week containing this date")
    tl.set_defaults(func=cmd_timer_list)

    tis = sub.add_parser("timers", parents=[common], help="the week's timers (same as `timer list`)")
    tis.add_argument("--date", type=_parse_date, default=date.today(), help="the week containing this date")
    tis.set_defaults(func=cmd_timer_list)

    fv = sub.add_parser("fav", help="favourite project/job pairs for the timer widget")
    fsub = fv.add_subparsers(dest="action", required=True)
    fsub.add_parser("list", parents=[common]).set_defaults(func=cmd_fav_list)
    fsub.add_parser("add", parents=[common, target]).set_defaults(func=cmd_fav_add)
    fr = fsub.add_parser("remove", parents=[common])
    fr.add_argument("id", help="the favourite id (ID column of `fav list`)")
    fr.set_defaults(func=cmd_fav_remove)

    cl = sub.add_parser("client", help="add or edit a client")
    csub = cl.add_subparsers(dest="action", required=True)
    ca = csub.add_parser("add", parents=[common])
    ca.add_argument("name")
    ca.add_argument("--type", choices=("c", "s", "u"), default="c", help="company, self-employed or unknown (default c)")
    ca.add_argument("--vat", help="VAT number")
    ca.set_defaults(func=cmd_client_add)
    ce = csub.add_parser("edit", parents=[common])
    ce.add_argument("client", help="client name substring or id")
    ce.add_argument("--name")
    ce.add_argument("--vat")
    ce.add_argument("--type", choices=("c", "s", "u"))
    ce.add_argument("--state", choices=("a", "i", "c"), help="active, inactive or closed")
    ce.set_defaults(func=cmd_client_edit)

    pr = sub.add_parser("project", help="add or edit a project")
    psub = pr.add_subparsers(dest="action", required=True)
    pa = psub.add_parser("add", parents=[common])
    pa.add_argument("name")
    pa.add_argument("--client", help="client name substring or id (business projects)")
    pa.add_argument("--jobs", required=True, help="comma-separated job names or ids")
    pa.add_argument("--team", help="team name or id (default: the only team)")
    pa.add_argument("--internal", action="store_true", help="an internal project (no client)")
    pa.add_argument("--description")
    pa.set_defaults(func=cmd_project_add)
    pe = psub.add_parser("edit", parents=[common])
    pe.add_argument("project", help="project name substring, code or id")
    pe.add_argument("--name")
    pe.add_argument("--description")
    pe.add_argument("--state", choices=("a", "i", "c"), help="active, on hold or closed")
    pe.add_argument("--add-job", action="append", default=[], metavar="JOB")
    pe.add_argument("--remove-job", action="append", default=[], metavar="JOB")
    pe.set_defaults(func=cmd_project_edit)

    jb = sub.add_parser("job", help="add or edit a job")
    jsub = jb.add_subparsers(dest="action", required=True)
    ja = jsub.add_parser("add", parents=[common])
    ja.add_argument("name")
    ja.add_argument("--type", choices=("b", "i", "p"), default="b", help="business, internal or personal (default b)")
    ja.add_argument("--description")
    ja.set_defaults(func=cmd_job_add)
    je = jsub.add_parser("edit", parents=[common])
    je.add_argument("job", help="job name or id")
    je.add_argument("--name")
    je.add_argument("--description")
    je.add_argument("--type", choices=("b", "i", "p"))
    je.add_argument("--active", dest="active", action="store_true", default=None)
    je.add_argument("--inactive", dest="active", action="store_false")
    je.set_defaults(func=cmd_job_edit)

    sub.add_parser("teams", parents=[common], help="list teams").set_defaults(func=cmd_teams)
    sub.add_parser("users", parents=[common], help="list users").set_defaults(func=cmd_users)

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
