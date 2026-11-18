"""Verschieben, Rezept wählen, Themenwechsel, Reste-Tag, Nachtrag and the list that follows the plan."""

import random
from datetime import date, timedelta

import pytest

from tischlein.config import PlanConfig
from tischlein.models import Recipe
from tischlein.service import Tischlein
from tischlein.store import Store

MONDAY = date(2026, 11, 16)
DAY = [MONDAY + timedelta(days=i) for i in range(5)]
RECIPES = {
    "h1": ("Hähnchen-Bowl", ("Hähnchen", "Kinder-OK"), "400 g Hähnchen\n1 Zwiebel"),
    "h2": ("Hähnchen-Curry", ("Hähnchen", "Kinder-OK"), "500 g Hähnchen\n1 Dose Kokosmilch"),
    "f1": ("Fischstäbchen", ("Fisch", "Kinder-OK"), "1 Packung Fischstäbchen"),
    "f2": ("Lachs-Pfanne", ("Fisch", "Kinder-OK"), "300 g Lachs"),
    "l1": ("Linsen-Dal", ("Hülsenfrüchte", "Kinder-OK"), "250 g Linsen"),
    "l2": ("Chili", ("Hülsenfrüchte", "Kinder-OK"), "1 Dose Bohnen\n500 g Rinderhack"),
    "p1": ("Spaghetti", ("Pasta/Reis", "Kinder-OK"), "500 g Spaghetti"),
    "p2": ("Risotto", ("Pasta/Reis",), "300 g Risottoreis"),
    "e1": ("Pfannkuchen", ("Eier/Quark", "Kinder-OK"), "4 Eier\n500 ml Milch"),
    "n1": ("Shakshuka", ("Eier/Quark", "Neu"), "6 Eier\n1 Dose Tomaten"),
}


@pytest.fixture
def svc():
    store = Store(":memory:")
    for uid, (name, tags, ingredients) in RECIPES.items():
        store.upsert_recipe(Recipe(uid, name, tags), uid, {"uid": uid, "name": name, "ingredients": ingredients})
    cfg = PlanConfig(new_slot=3, holiday_state="BW")
    return Tischlein(store, cfg, rng=random.Random(1))


def meals(plan):
    return {m.day: m for m in plan.meals}


def test_verschieben_swaps_two_days_and_keeps_the_theme_at_the_weekday(svc):
    plan = svc.generate(MONDAY)
    mon, wed = meals(plan)[DAY[0]], meals(plan)[DAY[2]]
    plan = meals(svc.move(DAY[2], DAY[0], today=MONDAY))
    assert plan[DAY[0]].recipe_uid == wed.recipe_uid and plan[DAY[0]].theme == "Hähnchen"
    assert plan[DAY[0]].reason == "Verschoben von Mittwoch"
    assert plan[DAY[2]].recipe_uid == mon.recipe_uid and plan[DAY[2]].theme == "Fisch"


def test_verschieben_also_in_an_approved_plan_but_not_into_the_past(svc):
    svc.generate(MONDAY)
    svc.set_status(MONDAY, "freigegeben")
    assert svc.move(DAY[1], DAY[4], today=MONDAY).status == "freigegeben"
    with pytest.raises(ValueError):
        svc.move(DAY[0], DAY[3], today=DAY[1])


def test_verschieben_onto_a_free_day_makes_the_other_day_free(svc):
    svc.generate(MONDAY)
    plan = svc.set_day_free(DAY[4], "Urlaub")
    dish = meals(plan)[DAY[1]]
    plan = meals(svc.move(DAY[1], DAY[4], today=MONDAY))
    assert plan[DAY[4]].recipe_uid == dish.recipe_uid and not plan[DAY[4]].skipped
    assert plan[DAY[1]].skipped and plan[DAY[1]].reason == "Urlaub"
    assert svc.skip_map(MONDAY) == {DAY[1]: "Urlaub"}


def test_verschieben_keeps_chosen_portions_with_the_dish(svc):
    plan = svc.generate(MONDAY)
    dish = meals(plan)[DAY[0]]
    svc.set_servings(DAY[0], 6)
    svc.move(DAY[0], DAY[2], today=MONDAY)
    assert svc.week_servings(MONDAY)[DAY[2]]["servings"] == 6
    assert svc.week_servings(MONDAY)[DAY[0]]["servings"] == 4
    assert meals(svc.store.plan(MONDAY))[DAY[2]].recipe_uid == dish.recipe_uid


def test_rezept_waehlen_stays_when_the_week_is_rolled_again(svc):
    svc.generate(MONDAY)
    plan = meals(svc.choose(DAY[0], "l2", today=MONDAY))
    assert plan[DAY[0]].recipe_uid == "l2" and plan[DAY[0]].chosen
    assert "Von Hand gewählt" in plan[DAY[0]].reason
    assert "l2" not in {m.recipe_uid for d, m in plan.items() if d != DAY[0]}
    for _ in range(5):
        assert meals(svc.generate(MONDAY))[DAY[0]].recipe_uid == "l2"


def test_rezept_waehlen_shows_rules_as_hints_only(svc):
    svc.generate(MONDAY)
    meal = meals(svc.choose(DAY[4], "p2", today=MONDAY))[DAY[4]]
    assert meal.recipe_uid == "p2" and "nicht Kinder-OK" in meal.reason


def test_a_new_dish_picked_by_hand_replaces_the_new_slot(svc):
    plan = meals(svc.generate(MONDAY))
    assert plan[DAY[3]].recipe_uid == "n1" and plan[DAY[3]].is_new
    svc.store.upsert_recipe(Recipe("n2", "Fischcurry", ("Fisch", "Neu")), "n2", {"ingredients": "300 g Fisch"})
    plan = meals(svc.choose(DAY[2], "n2", today=MONDAY))
    assert plan[DAY[2]].is_new
    assert not plan[DAY[3]].is_new and plan[DAY[3]].recipe_uid == "e1"


def test_recipe_choices_put_the_theme_first_and_flagged_dishes_last(svc):
    plan = meals(svc.generate(MONDAY))
    choices = svc.recipe_choices(DAY[2])
    fish = plan[DAY[2]].recipe_uid
    other_fish = ({"f1", "f2"} - {fish}).pop()
    assert choices[0]["uid"] == other_fish and not choices[0]["hints"]
    flagged = [c["uid"] for c in choices if c["hints"]]
    assert [c["uid"] for c in choices][-len(flagged) :] == flagged
    by_uid = {c["uid"]: c for c in choices}
    assert "nicht Kinder-OK" in by_uid["p2"]["hints"]
    assert by_uid[plan[DAY[0]].recipe_uid]["hints"][0] == "schon am Montag"


def test_themenwechsel_plans_the_day_again_and_survives_rolling(svc):
    svc.generate(MONDAY)
    plan = meals(svc.set_theme(DAY[2], "Pasta/Reis", today=MONDAY))
    assert plan[DAY[2]].theme == "Pasta/Reis" and plan[DAY[2]].recipe_uid in {"p1", "p2"}
    assert meals(svc.generate(MONDAY))[DAY[2]].theme == "Pasta/Reis"
    plan = meals(svc.set_theme(DAY[2], None, today=MONDAY))
    assert plan[DAY[2]].theme == "Fisch"
    with pytest.raises(ValueError):
        svc.set_theme(DAY[2], "Kuchen", today=MONDAY)


def test_reste_tag_takes_the_next_lunch_day(svc):
    plan = svc.generate(MONDAY)
    svc.set_day_free(DAY[1], "Kita / Schule")
    dish = meals(plan)[DAY[0]]
    plan = meals(svc.set_leftovers(DAY[0], True))
    assert plan[DAY[2]].leftovers and plan[DAY[2]].recipe_uid == dish.recipe_uid
    assert plan[DAY[2]].name == f"Reste: {dish.name}"
    assert DAY[2] not in svc.week_servings(MONDAY)  # nothing to cook, nothing to buy
    plan = meals(svc.set_leftovers(DAY[0], False))
    assert not plan[DAY[2]].leftovers


def test_no_leftovers_across_the_weekend(svc):
    svc.generate(MONDAY)
    with pytest.raises(ValueError):
        svc.set_leftovers(DAY[4], True)


def test_leftovers_move_with_their_dish(svc):
    svc.generate(MONDAY)
    chili = meals(svc.choose(DAY[0], "l2", today=MONDAY))[DAY[0]]
    svc.set_leftovers(DAY[0], True)
    plan = meals(svc.move(DAY[0], DAY[2], today=MONDAY))
    assert plan[DAY[2]].recipe_uid == chili.recipe_uid
    assert plan[DAY[3]].leftovers and plan[DAY[3]].recipe_uid == "l2"
    assert not plan[DAY[1]].leftovers and plan[DAY[1]].recipe_uid
    with pytest.raises(ValueError):
        svc.move(DAY[3], DAY[4], today=MONDAY)


def test_leftovers_go_when_their_dish_is_swapped(svc):
    svc.generate(MONDAY)
    svc.set_leftovers(DAY[0], True)
    plan = meals(svc.swap(MONDAY, DAY[0]))
    assert not plan[DAY[1]].leftovers


def test_nachtrag_replaces_the_planned_dish_and_counts_as_cooked(svc):
    plan = svc.generate(MONDAY)
    svc.set_status(MONDAY, "freigegeben")
    planned = meals(plan)[DAY[1]]
    svc.log_cooked(DAY[1], "p2", today=DAY[2])
    eaten = meals(svc.eaten_plan(MONDAY))
    assert eaten[DAY[1]].recipe_uid == "p2" and eaten[DAY[1]].logged
    assert planned.name in eaten[DAY[1]].reason
    last = svc.store.last_cooked(MONDAY + timedelta(weeks=1))
    assert last["p2"] == DAY[1] and planned.recipe_uid not in last
    assert svc.day_info(DAY[1])["gericht"] == "Risotto"
    assert "Risotto" in svc.calendar_ics()


def test_nachtrag_on_a_day_without_plan_and_as_free_text(svc):
    saturday = MONDAY + timedelta(days=5)
    svc.log_cooked(saturday, name="Pizza vom Bäcker", today=saturday)
    svc.log_cooked(DAY[0], "h2", today=saturday)  # no plan at all this week
    assert svc.store.last_cooked(saturday + timedelta(days=1)) == {"h2": DAY[0]}
    with pytest.raises(ValueError):
        svc.log_cooked(saturday + timedelta(days=1), name="Morgen", today=saturday)
    svc.remove_cooked(DAY[0])
    assert DAY[0] not in svc.store.cooked()


def test_shopping_list_follows_the_dishes_once_collected(svc):
    svc.generate(MONDAY)
    svc.swap(MONDAY, DAY[0])
    assert not svc.has_shopping(MONDAY)  # no list yet: nothing collected by itself
    svc.set_status(MONDAY, "freigegeben")  # approval collects the first list
    assert svc.has_shopping(MONDAY)

    item = svc.store.shopping(MONDAY)[0]
    svc.set_item_done(item["id"], True)
    before = {i["name"] for i in svc.store.shopping(MONDAY)}
    svc.choose(DAY[0], "l2", today=MONDAY)
    after = {i["name"] for i in svc.store.shopping(MONDAY)}
    assert "Bohnen" in " ".join(after) and after != before


def test_moving_alone_does_not_collect_again(svc, monkeypatch):
    svc.generate(MONDAY)
    svc.set_status(MONDAY, "freigegeben")
    calls = []
    monkeypatch.setattr(svc, "refresh_shopping", calls.append)
    svc.move(DAY[0], DAY[2], today=MONDAY)
    assert calls == []
    svc.set_leftovers(DAY[2], True)
    assert calls == [MONDAY]
