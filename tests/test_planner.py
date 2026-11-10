import random
from datetime import date, timedelta

import pytest

from tischlein.config import DayRule, PlanConfig
from tischlein.models import Recipe
from tischlein.planner import plan_week, week_start_of

MONDAY = date(2026, 11, 16)


def r(uid, *tags, rating=0):
    return Recipe(uid=uid, name=f"Rezept {uid}", tags=tags, rating=rating)


def cfg(*themes, new_slot=None, cooldown=2):
    return PlanConfig(
        days=tuple(DayRule(i, t) for i, t in enumerate(themes)),
        new_slot=new_slot,
        cooldown_weeks=cooldown,
    )


def by_day(meals):
    return {m.day.weekday(): m for m in meals}


def test_picks_recipe_with_theme_tag_for_each_day():
    recipes = [r("h", "Hähnchen", "Kinder-OK"), r("f", "Fisch", "Kinder-OK")]
    meals = by_day(plan_week(MONDAY, recipes, cfg("Hähnchen", "Fisch"), rng=random.Random(1)))
    assert meals[0].recipe_uid == "h"
    assert meals[1].recipe_uid == "f"


def test_prefers_kid_ok_and_falls_back_with_reason():
    recipes = [r("ok", "Fisch", "Kinder-OK"), r("adult", "Fisch", rating=5), r("l", "Linsen")]
    for seed in range(20):
        meal = plan_week(MONDAY, recipes, cfg("Fisch"), rng=random.Random(seed))[0]
        assert meal.recipe_uid == "ok"

    meal = plan_week(MONDAY, recipes, cfg("Linsen"), rng=random.Random(0))[0]
    assert meal.recipe_uid == "l"
    assert "Kinder-OK" in meal.reason


def test_no_recipe_twice_in_a_week():
    recipes = [r("a", "Fisch", "Kinder-OK"), r("b", "Fisch", "Kinder-OK")]
    meals = plan_week(MONDAY, recipes, cfg("Fisch", "Fisch"), rng=random.Random(3))
    assert {m.recipe_uid for m in meals} == {"a", "b"}


def test_respects_cooldown_and_relaxes_it_when_nothing_is_left():
    recipes = [r("recent", "Fisch", "Kinder-OK", rating=5), r("old", "Fisch", "Kinder-OK")]
    last = {"recent": MONDAY - timedelta(weeks=1), "old": MONDAY - timedelta(weeks=5)}
    for seed in range(20):
        assert plan_week(MONDAY, recipes, cfg("Fisch"), last, rng=random.Random(seed))[0].recipe_uid == "old"

    last = {"recent": MONDAY - timedelta(days=3), "old": MONDAY - timedelta(days=10)}
    meal = plan_week(MONDAY, recipes, cfg("Fisch"), last, rng=random.Random(0))[0]
    assert meal.recipe_uid == "old"  # least recently cooked
    assert "Cooldown" in meal.reason


def test_new_slot_prefers_new_recipe_matching_theme():
    recipes = [
        r("std", "Eier", "Kinder-OK"),
        r("new-other", "Fisch", "Neu"),
        r("new-eier", "Eier", "Neu"),
    ]
    meal = plan_week(MONDAY, recipes, cfg("Eier", new_slot=0), rng=random.Random(0))[0]
    assert meal.recipe_uid == "new-eier"
    assert meal.is_new
    assert "sichere Komponente" in meal.reason


def test_new_slot_takes_any_new_recipe_then_falls_back_to_rotation():
    recipes = [r("std", "Eier", "Kinder-OK"), r("new-other", "Fisch", "Neu", "Hauptspeisen")]
    assert plan_week(MONDAY, recipes, cfg("Eier", new_slot=0))[0].recipe_uid == "new-other"

    meal = plan_week(MONDAY, [r("std", "Eier", "Kinder-OK")], cfg("Eier", new_slot=0))[0]
    assert meal.recipe_uid == "std"
    assert not meal.is_new


def test_new_recipes_stay_out_of_regular_days():
    recipes = [r("new", "Fisch", "Neu", "Kinder-OK", rating=5), r("std", "Fisch", "Kinder-OK")]
    for seed in range(20):
        assert plan_week(MONDAY, recipes, cfg("Fisch"), rng=random.Random(seed))[0].recipe_uid == "std"


def test_feedback_overrides_tags():
    recipes = [r("tried", "Fisch", "Neu"), r("hated", "Fisch", "Kinder-OK", rating=5)]
    fb = {"tried": "ok", "hated": "nein"}
    meal = plan_week(MONDAY, recipes, cfg("Fisch"), feedback=fb, rng=random.Random(0))[0]
    assert meal.recipe_uid == "tried"
    assert meal.reason == ""  # counts as Kinder-OK now


def test_missing_theme_yields_placeholder():
    meal = plan_week(MONDAY, [], cfg("Fisch"))[0]
    assert meal.recipe_uid is None
    assert "Fisch" in meal.reason


def test_keep_preserves_days_and_blocks_their_recipes():
    recipes = [r("a", "Fisch", "Kinder-OK"), r("b", "Fisch", "Kinder-OK")]
    first = plan_week(MONDAY, recipes, cfg("Fisch", "Fisch"), rng=random.Random(0))
    kept = {first[0].day: first[0]}
    second = plan_week(MONDAY, recipes, cfg("Fisch", "Fisch"), keep=kept, rng=random.Random(99))
    assert second[0] == first[0]
    assert second[1].recipe_uid != first[0].recipe_uid


def test_week_start_must_be_monday():
    assert week_start_of(date(2026, 11, 19)) == MONDAY
    with pytest.raises(ValueError):
        plan_week(MONDAY + timedelta(days=1), [], cfg("Fisch"))


def test_exclude_forces_a_different_recipe():
    recipes = [r("a", "Fisch", "Kinder-OK", rating=5), r("b", "Fisch", "Kinder-OK")]
    for seed in range(10):
        meal = plan_week(MONDAY, recipes, cfg("Fisch"), exclude={"a"}, rng=random.Random(seed))[0]
        assert meal.recipe_uid == "b"


def test_new_slot_only_picks_lunch_dishes():
    """A new bread or cake must never become Thursday's lunch."""
    cfg_ = PlanConfig(days=(DayRule(0, "Eier"),), new_slot=0, extra_themes=("Fisch",))
    bread = r("brot", "Brot", "Neu")
    cake = r("kuchen", "Kuchen", "Neu", rating=5)
    main_new = r("haupt", "Hauptspeisen", "Neu")
    themed_new = r("fisch", "Fisch", "Neu")
    std = r("std", "Eier", "Kinder-OK")
    for seed in range(20):
        meal = plan_week(MONDAY, [bread, cake, std], cfg_, rng=random.Random(seed))[0]
        assert meal.recipe_uid == "std" and not meal.is_new
        meal = plan_week(MONDAY, [bread, cake, main_new, themed_new, std], cfg_, rng=random.Random(seed))[0]
        assert meal.recipe_uid in {"haupt", "fisch"} and meal.is_new


def test_skipped_days_get_no_meal_and_keep_their_reason():
    recipes = [r("a", "Fisch", "Kinder-OK"), r("b", "Fisch", "Kinder-OK")]
    meals = plan_week(
        MONDAY, recipes, cfg("Fisch", "Fisch"), skip={MONDAY: "Feiertag: Pfingstmontag"}, rng=random.Random(0)
    )
    assert meals[0].skipped and meals[0].recipe_uid is None
    assert meals[0].reason == "Feiertag: Pfingstmontag"
    assert meals[1].recipe_uid in {"a", "b"} and not meals[1].skipped


def test_rating_weights_shift_the_choice():
    recipes = [r("loved", "Fisch", "Kinder-OK"), r("meh", "Fisch", "Kinder-OK")]
    picks = [
        plan_week(MONDAY, recipes, cfg("Fisch"), weights={"loved": 3.0, "meh": 0.2}, rng=random.Random(s))[0].recipe_uid
        for s in range(200)
    ]
    assert picks.count("loved") > 150
