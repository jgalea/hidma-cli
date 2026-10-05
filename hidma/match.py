"""Pick one record by id or by case-insensitive name substring."""
from __future__ import annotations


class MatchError(ValueError):
    pass


def match_one(items: list[dict], query: str, label: str, *, names=("name",)) -> dict:
    """Exact id, then exact name (case-insensitive), then unique substring. MatchError when none or several match."""
    q = query.strip()
    for it in items:
        if str(it.get("id")) == q:
            return it
    ql = q.lower()

    def texts(it):
        return [str(it.get(n) or "") for n in names]

    exact = [it for it in items if any(t.lower() == ql for t in texts(it))]
    if len(exact) == 1:
        return exact[0]
    hits = exact or [it for it in items if any(ql in t.lower() for t in texts(it))]
    if not hits:
        raise MatchError(f"no {label} matches {query!r}")
    if len(hits) > 1:
        shown = ", ".join(f"{texts(it)[0]} ({it.get('id')})" for it in hits[:8])
        more = "" if len(hits) <= 8 else f" and {len(hits) - 8} more"
        raise MatchError(f"{label} {query!r} is ambiguous: {shown}{more}")
    return hits[0]
