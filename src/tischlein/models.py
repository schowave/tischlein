"""Domain objects. Pure data, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

PlanStatus = Literal["entwurf", "freigegeben"]
Verdict = Literal["ok", "nein"]


@dataclass(frozen=True)
class Recipe:
    uid: str
    name: str
    tags: tuple[str, ...] = ()
    rating: int = 0
    source_url: str = ""
    total_time: str = ""

    def has_tag(self, tag: str) -> bool:
        wanted = tag.casefold()
        return any(t.casefold() == wanted for t in self.tags)


@dataclass(frozen=True)
class Meal:
    day: date
    recipe_uid: str | None
    name: str
    theme: str
    is_new: bool = False
    reason: str = ""
    skipped: bool = False  # a day without lunch (holiday, away, ...)


@dataclass
class WeekPlan:
    week_start: date
    status: PlanStatus = "entwurf"
    meals: list[Meal] = field(default_factory=list)
