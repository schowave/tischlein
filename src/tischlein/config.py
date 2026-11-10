"""Load tischlein.toml into a PlanConfig."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

WEEKDAYS = {"mo": 0, "di": 1, "mi": 2, "do": 3, "fr": 4, "sa": 5, "so": 6}
WEEKDAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


@dataclass(frozen=True)
class DayRule:
    weekday: int  # 0 = Monday
    theme: str


@dataclass(frozen=True)
class PlanConfig:
    days: tuple[DayRule, ...] = field(
        default_factory=lambda: (
            DayRule(0, "Hähnchen"),
            DayRule(1, "Hülsenfrüchte"),
            DayRule(2, "Fisch"),
            DayRule(3, "Eier/Quark"),
            DayRule(4, "Pasta/Reis"),
        )
    )
    kid_tag: str = "Kinder-OK"
    new_tag: str = "Neu"
    main_tag: str = "Hauptspeisen"
    ai_tag: str = "KI"  # marks recipes created from an LLM idea
    new_slot: int | None = 3
    cooldown_weeks: int = 2
    lunch_time: time = time(12, 0)
    lunch_minutes: int = 45
    extra_themes: tuple[str, ...] = ()
    sync_minutes: int = 15  # automatic Paprika check interval, 0 = off
    holiday_state: str = ""  # Bundesland for Landesfeiertage, e.g. "BW"; "" = nur bundesweite
    pantry: tuple[str, ...] = (
        "Salz",
        "Pfeffer",
        "Zucker",
        "Mehl",
        "Öl",
        "Olivenöl",
        "Rapsöl",
        "Sonnenblumenöl",
        "Wasser",
        "Essig",
        "Paprikapulver",
        "Currypulver",
        "Gemüsebrühe",
        "Brühe",
    )
    reminder_time: str = "18:00"  # Vorabend-Erinnerung an Home Assistant, "" = aus

    @property
    def themes(self) -> tuple[str, ...]:
        """Protein themes: those of the weekly schedule, then extra ones without a day."""
        seen: list[str] = []
        for t in [d.theme for d in self.days] + list(self.extra_themes):
            if t.casefold() not in {s.casefold() for s in seen}:
                seen.append(t)
        return tuple(seen)


def _weekday(value: str) -> int:
    key = value.strip().lower()[:2]
    if key not in WEEKDAYS:
        raise ValueError(f"Unbekannter Wochentag: {value!r} (erwartet mo, di, mi, do, fr, sa, so)")
    return WEEKDAYS[key]


def parse_config(data: dict) -> PlanConfig:
    plan = data.get("plan", {})
    default = PlanConfig()
    days = tuple(DayRule(_weekday(d["tag"]), d["thema"]) for d in data.get("tage", [])) or default.days
    new_slot = plan.get("neu_slot", "do")
    hour, minute = (int(x) for x in plan.get("mittag_uhrzeit", "12:00").split(":"))
    return PlanConfig(
        days=tuple(sorted(days, key=lambda d: d.weekday)),
        kid_tag=plan.get("kinder_tag", default.kid_tag),
        new_tag=plan.get("neu_tag", default.new_tag),
        main_tag=plan.get("hauptspeise_tag", default.main_tag),
        ai_tag=plan.get("ki_tag", default.ai_tag),
        new_slot=_weekday(new_slot) if new_slot else None,
        cooldown_weeks=int(plan.get("cooldown_wochen", default.cooldown_weeks)),
        lunch_time=time(hour, minute),
        lunch_minutes=int(plan.get("mittag_minuten", default.lunch_minutes)),
        sync_minutes=int(data.get("abgleich", {}).get("alle_minuten", default.sync_minutes)),
        holiday_state=str(plan.get("feiertage_land", default.holiday_state)),
        pantry=tuple(data.get("einkauf", {}).get("vorrat", default.pantry)),
        reminder_time=str(data.get("erinnerung", {}).get("uhrzeit", default.reminder_time)),
        extra_themes=tuple(plan.get("weitere_themen", ())),
    )


def load_config(path: Path) -> PlanConfig:
    if not path.exists():
        return PlanConfig()
    with path.open("rb") as f:
        return parse_config(tomllib.load(f))
