"""Duration strings to minutes: 1h30, 1h 30m, 90m, 1.5h, 1:30, 90."""
from __future__ import annotations

import re

_HM = re.compile(r"^(?:(\d+(?:\.\d+)?)h)?(?:(\d+)(?:min|m)?)?$")
_COLON = re.compile(r"^(\d+):([0-5]\d)$")


def parse_minutes(text: str) -> int:
    """Return whole minutes for a duration string; ValueError when it does not parse or is zero."""
    s = text.strip().lower().replace(" ", "")
    if not s:
        raise ValueError("empty duration")
    if m := _COLON.match(s):
        minutes = int(m.group(1)) * 60 + int(m.group(2))
    elif s.isdigit():
        minutes = int(s)
    else:
        m = _HM.match(s)
        if not m or (m.group(1) is None and m.group(2) is None):
            raise ValueError(f"cannot read duration {text!r}; use 1h30, 90m, 1.5h or 1:30")
        hours = float(m.group(1)) if m.group(1) else 0.0
        mins = int(m.group(2)) if m.group(2) else 0
        minutes = round(hours * 60) + mins
    if minutes <= 0:
        raise ValueError("duration must be more than zero minutes")
    return minutes


def format_minutes(minutes: int) -> str:
    """90 -> '1h30', 60 -> '1h', 45 -> '45m', 0 -> '0m'."""
    minutes = int(minutes or 0)
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h{m:02d}"
    if h:
        return f"{h}h"
    return f"{m}m"
