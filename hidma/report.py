"""Totals, CSV and the weekly grid, all computed from flattened entries (see api.flatten)."""
from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timedelta

from .duration import format_minutes

GROUPS = ("client", "project", "job", "day", "week")
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def group_key(entry: dict, by: str) -> str:
    if by == "day":
        return entry["date"]
    if by == "week":
        d = datetime.strptime(entry["date"], "%Y-%m-%d").date()
        return str(d - timedelta(days=d.weekday()))
    if by == "client":
        return entry["client"] or "(no client)"
    if by == "project":
        return " / ".join(x for x in (entry["client"], entry["project"]) if x) or "(no project)"
    if by == "job":
        return " / ".join(x for x in (entry["client"], entry["project"], entry["job"]) if x)
    raise ValueError(f"--by must be one of {', '.join(GROUPS)}")


def aggregate(entries: list[dict], by: str) -> list[dict]:
    """Rows of {key, minutes, billable, not_billable, entries} sorted by key (dates) or by minutes desc (names)."""
    rows: dict[str, dict] = {}
    for e in entries:
        r = rows.setdefault(group_key(e, by), {"key": None, "minutes": 0, "not_billable": 0, "entries": 0})
        r["minutes"] += e["minutes"]
        r["not_billable"] += e["not_billable"]
        r["entries"] += 1
    out = []
    for key, r in rows.items():
        out.append({"key": key, "minutes": r["minutes"], "billable": r["minutes"] - r["not_billable"],
                    "not_billable": r["not_billable"], "entries": r["entries"]})
    if by in ("day", "week"):
        return sorted(out, key=lambda r: r["key"])
    return sorted(out, key=lambda r: (-r["minutes"], r["key"]))


def write_csv(rows: list[dict], columns: list[str], out=None) -> None:
    writer = csv.writer(out or sys.stdout)
    writer.writerow(columns)
    for r in rows:
        writer.writerow([r.get(c, "") for c in columns])


def week_grid(entries: list[dict], monday: date, status: str = "") -> str:
    """The app's weekly table: one row per client/project/job, a column per day, totals both ways."""
    days = [monday + timedelta(days=i) for i in range(7)]
    rows: dict[str, dict[str, int]] = {}
    for e in entries:
        label = " / ".join(x for x in (e["client"], e["project"], e["job"]) if x)
        rows.setdefault(label, {})
        rows[label][e["date"]] = rows[label].get(e["date"], 0) + e["minutes"]
    if not rows:
        return f"week of {monday} ({monday} to {days[-1]}): no entries" + (f"  [{status}]" if status else "")
    labels = sorted(rows, key=lambda k: -sum(rows[k].values()))
    width = max(len(l) for l in labels + ["TOTAL"])
    cell_w = 6
    head = "".ljust(width) + "".join(f"{d.day:>2} {DAY_NAMES[d.weekday()]}".rjust(cell_w + 1) for d in days) + "   TOTAL".rjust(cell_w + 3)
    lines = [f"week of {monday} ({monday} to {days[-1]})" + (f"  [{status}]" if status else ""), head]
    col_totals = {str(d): 0 for d in days}
    for label in labels:
        cells = []
        for d in days:
            m = rows[label].get(str(d), 0)
            col_totals[str(d)] += m
            cells.append((format_minutes(m) if m else "-").rjust(cell_w + 1))
        lines.append(label.ljust(width) + "".join(cells) + format_minutes(sum(rows[label].values())).rjust(cell_w + 3))
    lines.append("TOTAL".ljust(width) + "".join((format_minutes(col_totals[str(d)]) if col_totals[str(d)] else "-").rjust(cell_w + 1) for d in days)
                 + format_minutes(sum(col_totals.values())).rjust(cell_w + 3))
    return "\n".join(lines)


def money(cents) -> str:
    try:
        return f"{int(cents) / 100:,.2f}"
    except (TypeError, ValueError):
        return ""
