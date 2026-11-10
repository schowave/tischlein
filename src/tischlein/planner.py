"""Rotation planner. Pure functions: all state comes in as arguments."""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Mapping
from datetime import date, timedelta

from .config import DayRule, PlanConfig
from .models import Meal, Recipe, Verdict

SAFE_HINT = "Neu-Slot: sichere Komponente dazu servieren (z. B. Nudeln, Brot, Gurke)."
QUICK_MINUTES = 30
QUICK_TAG = "Schnell"
AVOID_REASON = "Enthält etwas, das die Kinder nicht mögen; nichts anderes zum Thema übrig"
QUICK_REASON = "Kein schnelles Rezept zum Thema"


def _hours(part: str) -> float:
    """'1', '1.5', '1 1/2', '1½', '1 ½', '½' -> hours."""
    total = 0.0
    for p in re.findall(r"\d+/\d+|\d+(?:\.\d+)?|½|¼|¾", part):
        total += {"½": 0.5, "¼": 0.25, "¾": 0.75}.get(p) or (
            int(p.split("/")[0]) / int(p.split("/")[1]) if "/" in p else float(p)
        )
    return total


def minutes(text: str) -> int | None:
    """Paprika's free-text time in minutes: "25 Min.", "1 Std. 15 Min.", "1:30", "1h30", "1½ Std.",
    "1 1/2 Std."; several parts ("10 Min. + 20 Min. Backzeit") are added up."""
    t = (text or "").casefold().replace(",", ".")
    if m := re.fullmatch(r"\s*(\d+):(\d{2})\s*", t):
        return int(m.group(1)) * 60 + int(m.group(2))
    if m := re.fullmatch(r"\s*(\d+)\s*h\s*(\d+)\s*(?:min\.?)?\s*", t):  # "1h30"
        return int(m.group(1)) * 60 + int(m.group(2))
    num = r"(?:\d+(?:\.\d+)?(?:\s*(?:\d/\d|[½¼¾]))?|\d/\d|[½¼¾])"
    hours = re.findall(rf"({num})\s*(?:std|stunde|h(?![a-z])|hour|hr)", t)
    mins = re.findall(r"(\d+)\s*(?:min|m(?![a-z]))", t)
    if not hours and not mins:
        return None
    return int(sum(_hours(h) for h in hours) * 60 + sum(int(m) for m in mins))


def is_quick(r: Recipe) -> bool:
    n = minutes(r.total_time)
    return r.has_tag(QUICK_TAG) or (n is not None and n <= QUICK_MINUTES)


def _prefer(pool: list[Recipe], keep, reason: str) -> tuple[list[Recipe], str]:
    """The part of the pool that passes `keep`, or the whole pool with a reason when none does."""
    preferred = [r for r in pool if keep(r)]
    return (preferred, "") if preferred else (pool, reason if pool else "")


def week_start_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


class _View:
    """Recipes as the planner sees them: Paprika tags overlaid with local feedback."""

    def __init__(self, recipes: Iterable[Recipe], config: PlanConfig, feedback: Mapping[str, Verdict]):
        self.config = config
        self.feedback = feedback
        self.recipes = [r for r in recipes if feedback.get(r.uid) != "nein"]

    def kid_ok(self, r: Recipe) -> bool:
        return self.feedback.get(r.uid) == "ok" or r.has_tag(self.config.kid_tag)

    def is_new(self, r: Recipe) -> bool:
        return r.uid not in self.feedback and r.has_tag(self.config.new_tag)

    def is_lunch(self, r: Recipe) -> bool:
        """Main dishes and anything with a protein theme; keeps bread and cake out of the new slot."""
        return r.has_tag(self.config.main_tag) or any(r.has_tag(t) for t in self.config.themes)

    def with_theme(self, theme: str) -> list[Recipe]:
        return [r for r in self.recipes if r.has_tag(theme)]


def _pick(
    candidates: list[Recipe],
    day: date,
    taken: set[str],
    last_cooked: Mapping[str, date],
    cooldown_weeks: int,
    rng: random.Random,
    weights: Mapping[str, float] | None = None,
) -> tuple[Recipe | None, str]:
    pool = [r for r in candidates if r.uid not in taken]
    if not pool:
        return None, ""
    cutoff = day - timedelta(weeks=cooldown_weeks)
    fresh = [r for r in pool if last_cooked.get(r.uid, date.min) <= cutoff]
    if fresh:
        factors = weights or {}
        w = [(r.rating + 1) * factors.get(r.uid, 1.0) for r in fresh]
        return rng.choices(fresh, weights=w, k=1)[0], ""
    oldest = min(pool, key=lambda r: (last_cooked.get(r.uid, date.min), r.name))
    return oldest, "Cooldown gelockert: zu wenige Rezepte für dieses Thema"


def plan_day(
    day: date,
    rule: DayRule,
    view: _View,
    taken: set[str],
    last_cooked: Mapping[str, date],
    rng: random.Random,
    weights: Mapping[str, float] | None = None,
    avoid: set[str] = frozenset(),
    quick: bool = False,
) -> Meal:
    cfg = view.config
    themed = view.with_theme(rule.theme)

    if cfg.new_slot == rule.weekday:
        new = [r for r in view.recipes if view.is_new(r) and view.is_lunch(r)]
        new = [r for r in new if r.uid not in avoid]
        if quick:  # a quick day keeps the new slot only for a quick new dish
            new = [r for r in new if is_quick(r)]
        new_themed = [r for r in new if r in themed]
        for pool in (new_themed, new):
            recipe, _ = _pick(pool, day, taken, last_cooked, 0, rng, weights)
            if recipe:
                return Meal(day, recipe.uid, recipe.name, rule.theme, is_new=True, reason=SAFE_HINT)

    regular = [r for r in themed if not view.is_new(r)]
    kid_ok = [r for r in regular if view.kid_ok(r)]
    reason = ""
    pool = kid_ok
    if not kid_ok:
        pool = regular
        reason = f"Kein {cfg.kid_tag}-Rezept zum Thema {rule.theme}"
    # drop dishes already planned this week first: a taken dish must not count as the alternative
    pool, avoid_reason = _prefer([r for r in pool if r.uid not in taken], lambda r: r.uid not in avoid, AVOID_REASON)
    quick_reason = ""
    if quick:
        pool, quick_reason = _prefer(pool, is_quick, QUICK_REASON)
    recipe, pick_reason = _pick(pool, day, taken, last_cooked, cfg.cooldown_weeks, rng, weights)
    if recipe is None:
        return Meal(
            day,
            None,
            f"Freie Wahl ({rule.theme})",
            rule.theme,
            reason=f"Kein Rezept mit Kategorie „{rule.theme}“ gefunden",
        )
    reasons = (reason, avoid_reason, quick_reason, pick_reason)
    return Meal(day, recipe.uid, recipe.name, rule.theme, reason="; ".join(x for x in reasons if x))


def plan_week(
    week_start: date,
    recipes: Iterable[Recipe],
    config: PlanConfig,
    last_cooked: Mapping[str, date] | None = None,
    feedback: Mapping[str, Verdict] | None = None,
    rng: random.Random | None = None,
    keep: Mapping[date, Meal] | None = None,
    exclude: Iterable[str] = (),
    skip: Mapping[date, str] | None = None,
    weights: Mapping[str, float] | None = None,
    avoid: Iterable[str] = (),
    quick: Iterable[date] = (),
) -> list[Meal]:
    """Plan lunches for the week starting on week_start (a Monday).

    Meals in `keep` stay as they are; only the other days are (re)planned.
    Recipes in `exclude` are not picked (used when swapping a single day).
    Days in `skip` (date -> reason) get no lunch. `weights` multiplies a recipe's chance
    (from the family's ratings; 1.0 = neutral). Recipes in `avoid` (an ingredient the children
    don't like) are only picked when nothing else fits; on `quick` days dishes up to 30 minutes
    (or tagged "Schnell") come first.
    """
    if week_start.weekday() != 0:
        raise ValueError("week_start muss ein Montag sein")
    last_cooked = last_cooked or {}
    keep = keep or {}
    rng = rng or random.Random()
    view = _View(recipes, config, feedback or {})
    avoid, quick = set(avoid), set(quick)

    taken = {m.recipe_uid for m in keep.values() if m.recipe_uid} | set(exclude)
    meals: list[Meal] = []
    for rule in config.days:
        day = week_start + timedelta(days=rule.weekday)
        if skip and day in skip:
            meal = Meal(day, None, "Kein Mittagessen", rule.theme, reason=skip[day], skipped=True)
        else:
            meal = keep.get(day) or plan_day(
                day, rule, view, taken, last_cooked, rng, weights, avoid=avoid, quick=day in quick
            )
        if meal.recipe_uid:
            taken.add(meal.recipe_uid)
        meals.append(meal)
    return meals
