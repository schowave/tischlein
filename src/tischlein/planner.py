"""Rotation planner. Pure functions: all state comes in as arguments."""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping
from datetime import date, timedelta

from .config import DayRule, PlanConfig
from .models import Meal, Recipe, Verdict

SAFE_HINT = "Neu-Slot: sichere Komponente dazu servieren (z. B. Nudeln, Brot, Gurke)."


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
) -> tuple[Recipe | None, str]:
    pool = [r for r in candidates if r.uid not in taken]
    if not pool:
        return None, ""
    cutoff = day - timedelta(weeks=cooldown_weeks)
    fresh = [r for r in pool if last_cooked.get(r.uid, date.min) <= cutoff]
    if fresh:
        weights = [r.rating + 1 for r in fresh]
        return rng.choices(fresh, weights=weights, k=1)[0], ""
    oldest = min(pool, key=lambda r: (last_cooked.get(r.uid, date.min), r.name))
    return oldest, "Cooldown gelockert: zu wenige Rezepte für dieses Thema"


def plan_day(
    day: date,
    rule: DayRule,
    view: _View,
    taken: set[str],
    last_cooked: Mapping[str, date],
    rng: random.Random,
) -> Meal:
    cfg = view.config
    themed = view.with_theme(rule.theme)

    if cfg.new_slot == rule.weekday:
        new = [r for r in view.recipes if view.is_new(r) and view.is_lunch(r)]
        new_themed = [r for r in new if r in themed]
        for pool in (new_themed, new):
            recipe, _ = _pick(pool, day, taken, last_cooked, 0, rng)
            if recipe:
                return Meal(day, recipe.uid, recipe.name, rule.theme, is_new=True, reason=SAFE_HINT)

    regular = [r for r in themed if not view.is_new(r)]
    kid_ok = [r for r in regular if view.kid_ok(r)]
    reason = ""
    pool = kid_ok
    if not kid_ok:
        pool = regular
        reason = f"Kein {cfg.kid_tag}-Rezept zum Thema {rule.theme}"
    recipe, pick_reason = _pick(pool, day, taken, last_cooked, cfg.cooldown_weeks, rng)
    if recipe is None:
        return Meal(day, None, f"Freie Wahl ({rule.theme})", rule.theme,
                    reason=f"Kein Rezept mit Kategorie „{rule.theme}“ gefunden")
    return Meal(day, recipe.uid, recipe.name, rule.theme,
                reason="; ".join(x for x in (reason, pick_reason) if x))


def plan_week(
    week_start: date,
    recipes: Iterable[Recipe],
    config: PlanConfig,
    last_cooked: Mapping[str, date] | None = None,
    feedback: Mapping[str, Verdict] | None = None,
    rng: random.Random | None = None,
    keep: Mapping[date, Meal] | None = None,
    exclude: Iterable[str] = (),
) -> list[Meal]:
    """Plan lunches for the week starting on week_start (a Monday).

    Meals in `keep` stay as they are; only the other days are (re)planned.
    Recipes in `exclude` are not picked (used when swapping a single day).
    """
    if week_start.weekday() != 0:
        raise ValueError("week_start muss ein Montag sein")
    last_cooked = last_cooked or {}
    keep = keep or {}
    rng = rng or random.Random()
    view = _View(recipes, config, feedback or {})

    taken = {m.recipe_uid for m in keep.values() if m.recipe_uid} | set(exclude)
    meals: list[Meal] = []
    for rule in config.days:
        day = week_start + timedelta(days=rule.weekday)
        meal = keep.get(day) or plan_day(day, rule, view, taken, last_cooked, rng)
        if meal.recipe_uid:
            taken.add(meal.recipe_uid)
        meals.append(meal)
    return meals
