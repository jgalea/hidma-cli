from hidma.api import _single_timesheet


def test_live_week_envelope_is_unwrapped():
    payload = {"data": {"timesheet": {"id": "ts1", "startDate": "2026-10-05", "endDate": "2026-10-11",
                                      "rows": [{"id": "r1", "cells": [{"id": "c1", "minutes": 120, "date": "2026-10-05"}]}]}}}
    ts = _single_timesheet(payload)
    assert ts["id"] == "ts1" and ts["rows"][0]["cells"][0]["minutes"] == 120


def test_non_timesheet_payload_is_not_unwrapped():
    assert _single_timesheet({"data": [{"id": "x"}]}) is None
    assert _single_timesheet([]) is None
