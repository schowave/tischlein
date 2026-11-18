"""Domain objects. Pure data, no I/O."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
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
    chosen: bool = False  # picked by hand: stays when the week is re-rolled
    leftovers: bool = False  # eats what the previous lunch day cooked; no cooking, no shopping
    logged: bool = False  # a Nachtrag: cooked although not (or not so) planned


@dataclass(frozen=True)
class Cooked:
    """A Nachtrag: what was cooked on a day when it was not the plan. No recipe = free text."""

    day: date
    recipe_uid: str | None
    name: str


@dataclass
class WeekPlan:
    week_start: date
    status: PlanStatus = "entwurf"
    meals: list[Meal] = field(default_factory=list)


def with_cooked(meals: list[Meal], cooked: Mapping[date, Cooked], days: tuple[date, date] | None = None) -> list[Meal]:
    """What was actually eaten: a Nachtrag replaces the meal of its day ("stattdessen"); on a day
    without a meal it is added. `days` (first, last) limits the added ones to a week."""
    out, seen = [], set()
    for m in meals:
        c = cooked.get(m.day)
        seen.add(m.day)
        if c is None:
            out.append(m)
            continue
        why = f"Stattdessen gekocht, geplant war {m.name}" if m.recipe_uid and not m.skipped else "Nachgetragen"
        out.append(
            replace(
                m,
                recipe_uid=c.recipe_uid,
                name=c.name,
                is_new=False,
                reason=why,
                skipped=False,
                chosen=False,
                leftovers=False,
                logged=True,
            )
        )
    for d, c in cooked.items():
        if d not in seen and (days is None or days[0] <= d <= days[1]):
            out.append(Meal(d, c.recipe_uid, c.name, "", reason="Nachgetragen", logged=True))
    return sorted(out, key=lambda m: m.day)
