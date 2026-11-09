"""Render approved plans as an iCalendar feed (RFC 5545), floating local time."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from .config import PlanConfig
from .models import WeekPlan


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> list[str]:
    """Fold lines longer than 75 octets."""
    out, current = [], b""
    for ch in line:
        enc = ch.encode()
        if len(current) + len(enc) > (75 if not out else 74):
            out.append(current.decode())
            current = b""
        current += enc
    out.append(current.decode())
    return [out[0]] + [" " + part for part in out[1:]]


def render(plans: Iterable[WeekPlan], config: PlanConfig, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Tischlein//Essensplan//DE",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Tischlein Mittagessen",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
    ]
    for plan in plans:
        for meal in plan.meals:
            start = datetime.combine(meal.day, config.lunch_time)
            end = start + timedelta(minutes=config.lunch_minutes)
            summary = ("Neu: " if meal.is_new else "") + meal.name
            desc = "\n".join(x for x in (f"Thema: {meal.theme}", meal.reason) if x)
            lines += [
                "BEGIN:VEVENT",
                f"UID:{meal.day.isoformat()}-mittag@tischlein",
                f"DTSTAMP:{stamp}",
                f"DTSTART:{start:%Y%m%dT%H%M%S}",
                f"DTEND:{end:%Y%m%dT%H%M%S}",
                f"SUMMARY:{_escape(summary)}",
                f"DESCRIPTION:{_escape(desc)}",
                "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "".join(part + "\r\n" for line in lines for part in _fold(line))
