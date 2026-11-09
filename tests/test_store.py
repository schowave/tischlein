from datetime import date

from tischlein.models import Meal, Recipe, WeekPlan
from tischlein.store import Store

MONDAY = date(2026, 11, 16)


def test_recipes_roundtrip_and_reimport_replaces():
    s = Store(":memory:")
    s.replace_recipes([Recipe("a", "Bowl", ("Hähnchen", "Kinder-OK"), 4)])
    assert s.recipes() == [Recipe("a", "Bowl", ("Hähnchen", "Kinder-OK"), 4)]
    s.replace_recipes([Recipe("b", "Dal")])
    assert [r.uid for r in s.recipes()] == ["b"]


def test_plan_roundtrip_and_last_cooked_only_counts_approved():
    s = Store(":memory:")
    meal = Meal(MONDAY, "a", "Bowl", "Hähnchen", True, "Hinweis")
    s.save_plan(WeekPlan(MONDAY, "entwurf", [meal]))
    assert s.plan(MONDAY) == WeekPlan(MONDAY, "entwurf", [meal])
    assert s.last_cooked(date(2027, 1, 1)) == {}

    s.save_plan(WeekPlan(MONDAY, "freigegeben", [meal]))
    assert s.last_cooked(date(2027, 1, 1)) == {"a": MONDAY}
    assert s.last_cooked(MONDAY) == {}
    assert [p.week_start for p in s.approved_plans()] == [MONDAY]


def test_feedback_upserts():
    s = Store(":memory:")
    s.set_feedback("a", "ok")
    s.set_feedback("a", "nein")
    assert s.feedback() == {"a": "nein"}
