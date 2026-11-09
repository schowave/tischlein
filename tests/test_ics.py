from datetime import date, datetime, timezone

from tischlein.config import PlanConfig
from tischlein.ics import render
from tischlein.models import Meal, WeekPlan


def test_renders_events_with_escaping_and_crlf():
    plan = WeekPlan(date(2026, 11, 16), "freigegeben", [
        Meal(date(2026, 11, 19), "x", "Shakshuka, scharf; mit Brot", "Eier/Quark", True, "Hinweis"),
    ])
    out = render([plan], PlanConfig(), now=datetime(2026, 11, 9, tzinfo=timezone.utc))

    assert out.startswith("BEGIN:VCALENDAR\r\n") and out.endswith("END:VCALENDAR\r\n")
    assert "DTSTART:20261119T120000\r\n" in out
    assert "DTEND:20261119T124500\r\n" in out
    assert "SUMMARY:Neu: Shakshuka\\, scharf\\; mit Brot\r\n" in out
    assert "UID:2026-11-19-mittag@tischlein\r\n" in out


def test_folds_long_lines():
    plan = WeekPlan(date(2026, 11, 16), "freigegeben", [Meal(date(2026, 11, 16), "x", "Ä" * 80, "T")])
    for line in render([plan], PlanConfig()).split("\r\n"):
        assert len(line.encode()) <= 75
