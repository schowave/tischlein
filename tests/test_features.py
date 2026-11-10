import json
import random
from datetime import date, datetime, timedelta

import pytest
from test_tagging import WritablePaprika, client

from tischlein import service as service_mod
from tischlein.config import DayRule, PlanConfig
from tischlein.llm import LLMError
from tischlein.service import Tischlein
from tischlein.store import Store

MONDAY = date(2026, 11, 16)


class FakeLLM:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, headers, body):
        self.calls.append(json.loads(body))
        answer = self.answers.pop(0)
        if isinstance(answer, int):
            return answer, b"nope"
        return 200, json.dumps({"choices": [{"message": {"content": json.dumps(answer)}}]}).encode()


@pytest.fixture
def svc():
    fake = WritablePaprika()
    fake.recipes["R1"].update(name="Bowl", ingredients="400 g Hähnchen\n1 Zwiebel\nSalz", categories=["C-HS"])
    fake.recipes["R2"].update(name="Püree", ingredients="1 kg Kartoffeln\n1 Zwiebel\n2 EL Öl", categories=["C-HS"])
    cfg = PlanConfig(
        days=(DayRule(0, "Hauptspeisen"), DayRule(1, "Hauptspeisen")),
        new_slot=None,
        holiday_state="BW",
        reminder_time="18:00",
    )
    s = Tischlein(Store(":memory:"), cfg, rng=random.Random(0), paprika=client(fake))
    s.sync_paprika()
    s.fake = fake
    return s


def test_skip_days_holidays_and_override(svc):
    christmas_week = date(2026, 12, 21)
    assert svc.skip_map(christmas_week)[date(2026, 12, 25)] == "Feiertag: 1. Weihnachtstag"

    svc.generate(MONDAY)
    plan = svc.set_day_free(MONDAY, "Urlaub")
    assert plan.meals[0].skipped and plan.meals[0].reason == "Urlaub"
    plan = svc.set_day_cooking(MONDAY)
    assert not plan.meals[0].skipped and plan.meals[0].recipe_uid

    allerheiligen = date(2027, 11, 1)  # a Monday
    assert allerheiligen in svc.skip_map(allerheiligen)
    svc.set_day_cooking(allerheiligen)
    assert allerheiligen not in svc.skip_map(allerheiligen)


def test_ratings_shape_weights_and_never_again_excludes(svc):
    svc.rate("R1", MONDAY, "gut", "David")
    svc.rate("R2", MONDAY, "schlecht", "Julia")
    w = svc.rating_weights()
    assert w["R1"] > 1.0 > w["R2"]
    svc.rate("R2", date(2026, 11, 17), "nie")
    assert svc.store.feedback()["R2"] == "nein"
    with pytest.raises(ValueError):
        svc.rate("R1", MONDAY, "super")


def test_llm_settings_are_tested_before_saving(svc):
    svc._llm_transport = FakeLLM([401])
    with pytest.raises(LLMError, match="abgelehnt"):
        svc.save_llm("https://llm.example", "sk-x", "gpt")
    assert svc.llm() is None

    svc._llm_transport = FakeLLM([{"ok": True}])
    svc.save_llm("https://llm.example/", "sk-x", "gpt")
    assert svc.llm_settings() == {
        "base_url": "https://llm.example/",
        "model": "gpt",
        "hat_schluessel": True,
        "aktiv": True,
    }
    svc._llm_transport = FakeLLM([{"ok": True}])
    svc.save_llm("https://llm.example/", "", "gpt-2")  # empty key keeps the stored one
    assert svc.store.meta("llm_api_key") == "sk-x"


def test_shopping_list_with_and_without_llm(svc):
    svc.generate(MONDAY)
    assert svc.generate_shopping(MONDAY) == "einfach"
    rows = {i["name"]: i for i in svc.store.shopping(MONDAY)}
    assert "Salz" not in rows and "Öl" not in rows
    assert rows["Zwiebel"]["amount"] == "2" and set(rows["Zwiebel"]["recipes"].split(", ")) == {"Bowl", "Püree"}
    assert rows["Hähnchen"]["group"] == "Fleisch & Fisch" and rows["Hähnchen"]["amount"] == "400 g"

    bowl_day = next(m.day for m in svc.store.plan(MONDAY).meals if m.name == "Bowl")
    assert svc.week_servings(MONDAY)[bowl_day] == {"base": 4, "servings": 4, "custom": False}
    assert svc.set_servings(bowl_day, 6) == 6  # the Bowl for six instead of four
    svc.generate_shopping(MONDAY)
    rows = {i["name"]: i for i in svc.store.shopping(MONDAY)}
    assert rows["Hähnchen"]["amount"] == "600 g" and rows["Zwiebel"]["amount"] == "2½"
    svc.set_servings(bowl_day, 7)
    svc.generate(MONDAY)  # re-rolled: an override whose dish moved or left the day is dropped
    kept = svc.store.servings(MONDAY, MONDAY + timedelta(days=6))
    assert all(any(m.day == d and m.recipe_uid == u for m in svc.store.plan(MONDAY).meals) for d, u in kept)
    bowl_day = next(m.day for m in svc.store.plan(MONDAY).meals if m.name == "Bowl")
    svc.set_servings(bowl_day, 4)  # back to the family's portions removes the override
    assert svc.store.servings(MONDAY, MONDAY + timedelta(days=6)) == {}

    svc.fake.recipes["R1"].update(servings="2 Portion/en", hash="zwei")  # a recipe written for two
    svc.sync_paprika()
    assert svc.week_servings(MONDAY)[bowl_day] == {"base": 2, "servings": 4, "custom": False}
    svc.generate_shopping(MONDAY)
    assert {i["name"]: i for i in svc.store.shopping(MONDAY)}["Hähnchen"]["amount"] == "800 g"  # doubled for four

    svc.store.set_meta("llm_base_url", "u")
    svc.store.set_meta("llm_api_key", "k")
    svc.store.set_meta("llm_model", "m")
    fake = FakeLLM(
        [{"items": [{"name": "Zwiebeln", "amount": "2", "group": "Obst & Gemüse", "recipes": ["Bowl", "Püree"]}]}]
    )
    svc._llm_transport = fake
    assert svc.generate_shopping(MONDAY) == "llm"
    assert "800 g Hähnchen  (für: Bowl)" in fake.calls[0]["messages"][1]["content"]  # scaled lines go to the LLM
    assert [(i["name"], i["amount"]) for i in svc.store.shopping(MONDAY)] == [("Zwiebeln", "2")]

    svc._llm_transport = FakeLLM([500])
    assert svc.generate_shopping(MONDAY).startswith("einfach (LLM-Fehler")


def test_ideas_generate_and_accept_into_paprika(svc):
    with pytest.raises(LLMError):
        svc.generate_ideas()
    svc.store.set_meta("llm_base_url", "u")
    svc.store.set_meta("llm_api_key", "k")
    svc.store.set_meta("llm_model", "m")
    svc._llm_transport = FakeLLM(
        [
            {
                "ideen": [
                    {
                        "name": "Fisch-Tacos",
                        "thema": "hauptspeisen",
                        "zutaten": ["400 g Seelachs"],
                        "zubereitung": ["Braten"],
                    }
                ]
            }
        ]
    )
    assert svc.generate_ideas(1) == 1
    idea = svc.store.ideas()[0]
    recipe = svc.accept_idea(idea["id"])
    assert recipe.name == "Fisch-Tacos" and "Neu" in recipe.tags and "Hauptspeisen" in recipe.tags
    assert "KI" in recipe.tags
    created = svc.fake.recipes[recipe.uid]
    assert created["ingredients"] == "400 g Seelachs" and created["hash"]
    assert svc.store.idea(idea["id"])["status"] == "uebernommen"
    with pytest.raises(KeyError):
        svc.accept_idea(idea["id"])


def _idea(name, thema="Hauptspeisen"):
    return {"ideen": [{"name": name, "thema": thema, "zutaten": ["1 x"], "zubereitung": ["s"]}]}


def _with_llm(svc, answers):
    svc.store.set_meta("llm_base_url", "u")
    svc.store.set_meta("llm_api_key", "k")
    svc.store.set_meta("llm_model", "m")
    svc._llm_transport = FakeLLM(answers)
    return svc._llm_transport


def test_ideas_one_dish_per_request_with_progress(svc):
    fake = _with_llm(svc, [_idea("A"), _idea("B"), _idea("C")])
    steps = []
    assert svc.generate_ideas(3, progress=lambda d, t: steps.append((d, t))) == 3
    assert steps == [(0, 3), (1, 3), (2, 3), (3, 3)]
    prompts = [c["messages"][1]["content"] for c in fake.calls]
    assert all("Schlage 1 Gericht vor" in p for p in prompts)
    assert "Gerade schon vorgeschlagen" not in prompts[0] and "A; B" in prompts[2]


def test_ideas_survive_a_failed_request_and_fail_only_when_all_do(svc):
    _with_llm(svc, [500, _idea("B")])
    assert svc.generate_ideas(2) == 1
    _with_llm(svc, [500, 500])
    with pytest.raises(LLMError):
        svc.generate_ideas(2)


def test_ideas_in_background_report_state(svc):
    _with_llm(svc, [_idea("A"), _idea("B")])
    state = svc.start_ideas(2)
    assert state["laeuft"] and state["gesamt"] == 2
    svc._ideas_thread.join(5)
    assert svc.ideas_status() == {"laeuft": False, "fertig": 2, "gesamt": 2, "ergebnis": 2, "fehler": None}


def test_shopping_item_added_by_hand_notifies_home_assistant(svc, monkeypatch):
    sent = []
    monkeypatch.setattr(svc, "_post_webhook", lambda p: sent.append(p) is None)  # delivered
    monkeypatch.setattr(svc, "_post_async", lambda p, ok=None: (sent.append(p), ok and ok()))
    svc.add_shopping_item(MONDAY, "Kaffee", "500 g", "Julia")
    assert sent == []  # no webhook configured
    svc.webhook_url = "http://ha/hook"
    svc.add_shopping_item(MONDAY, "Kaffee", "500 g", "Julia")
    note = next(p for p in sent if p["art"] == "einkauf")
    assert note["nachricht"] == "Julia hat Kaffee (500 g) auf die Liste gesetzt."
    assert len(svc.store.shopping(MONDAY)) == 2

    assert svc.send_to_bring(MONDAY, "Julia") == 2
    assert sent[-1]["art"] == "bring_sync" and sent[-1]["offen"][0] == {"name": "Kaffee", "menge": "500 g"}
    assert sent[-1]["liste"] == ""  # no list chosen: Home Assistant's default
    assert svc.save_bring_lists([{"entity_id": "todo.ikea", "name": "Ikea"}, {"entity_id": "light.x"}]) == 1
    svc.set_bring_list("todo.ikea")
    svc.send_to_bring(MONDAY)
    assert sent[-1]["liste"] == "todo.ikea"
    with pytest.raises(KeyError):
        svc.set_bring_list("todo.unbekannt")
    svc.save_bring_lists([])  # list gone in Home Assistant: back to the default
    assert svc.bring_list() == ""
    monkeypatch.setattr(svc, "_post_webhook", lambda p: False)  # Home Assistant down: say so
    with pytest.raises(LookupError):
        svc.send_to_bring(MONDAY)
    svc.webhook_url = ""
    with pytest.raises(LookupError):
        svc.send_to_bring(MONDAY)


def test_bring_sync_both_ways(svc, monkeypatch):
    sent = []
    monkeypatch.setattr(svc, "_post_async", lambda p, ok=None: (sent.append(p), ok and ok()))
    svc.webhook_url = "http://ha/hook"
    assert not svc.bring_sync_on()  # off until switched on in the settings
    svc.set_bring_sync(True)
    kaffee = svc.add_shopping_item(MONDAY, "Kaffee", "500 g")
    svc.add_shopping_item(MONDAY, "Milch", "")
    sync = [p for p in sent if p["art"] == "bring_sync"]
    assert all(i["bring"] == 1 for i in svc.store.shopping(MONDAY))  # both are in Bring now
    assert len(sync) == 2 and sync[0]["offen"] == [{"name": "Kaffee", "menge": "500 g"}]
    assert sync[1]["offen"] == [{"name": "Milch", "menge": ""}]  # only what changed: no re-adds

    svc.set_item_done(kaffee, True, "Julia")  # ticked here: ticked off in Bring
    assert sent[-1]["erledigt"] == ["Kaffee"] and sent[-1]["offen"] == []

    state = [
        {"summary": "Milch", "description": "", "status": "completed"},  # bought in Bring
        {"summary": "Brot", "description": "1 Laib", "status": "needs_action"},  # added in Bring
        {"summary": "Kaffee", "description": "500 g", "status": "completed"},
    ]
    assert svc.apply_bring_state("todo.zuhause", state, today=MONDAY) == {"abgehakt": 1, "neu": 1}
    rows = {i["name"]: i for i in svc.store.shopping(MONDAY)}
    assert rows["Milch"]["done"] and rows["Milch"]["done_by"] == "Bring"
    assert rows["Brot"]["group"] == "Aus Bring" and rows["Brot"]["amount"] == "1 Laib" and not rows["Brot"]["done"]
    assert svc.apply_bring_state("todo.zuhause", state, today=MONDAY) == {"abgehakt": 0, "neu": 0}  # idempotent
    assert svc.apply_bring_state("todo.zuhause", [], today=MONDAY) == {"abgehakt": 0, "neu": 0}  # failed read

    svc.save_bring_lists([{"entity_id": "todo.ikea", "name": "Ikea"}])
    svc.set_bring_list("todo.ikea")
    assert svc.apply_bring_state("todo.zuhause", state, today=MONDAY) == {"abgehakt": 0, "neu": 0}  # other list
    n = len(sent)
    svc.remove_done(MONDAY)  # both are already ticked off in Bring: nothing to send
    assert sent[n:] == [] and [i["name"] for i in svc.store.shopping(MONDAY)] == ["Brot"]
    svc.set_bring_sync(False)
    n = len(sent)
    svc.add_shopping_item(MONDAY, "Tee", "")
    assert all(p["art"] != "bring_sync" for p in sent[n:])  # sync off: only the push notice


def test_day_info_and_reminder(svc, monkeypatch):
    plan = svc.generate(MONDAY)
    changed = svc.fake.recipes[plan.meals[0].recipe_uid]
    changed["ingredients"] += "\n500 g TK Erbsen"
    changed["hash"] = "geaendert"  # Paprika gives an edited recipe a new hash
    svc.sync_paprika()
    info = svc.day_info(MONDAY)
    assert info["gericht"] == plan.meals[0].name and info["vorbereitung"]

    sent = []
    svc.webhook_url = "http://ha/webhook"
    monkeypatch.setattr(svc, "_post_webhook", sent.append)
    monkeypatch.setattr(svc, "_post_async", lambda p, ok=None: (sent.append(p), ok and ok()))
    sunday_evening = datetime(2026, 11, 15, 18, 5)
    assert svc.reminder_tick(sunday_evening) is False  # plan is only a draft
    svc.set_status(MONDAY, "freigegeben")
    svc.store.set_meta("erinnert_am", "")
    assert svc.reminder_tick(datetime(2026, 11, 15, 17, 0)) is False  # too early
    assert sent[0]["art"] == "freigabe" and "freigegeben" in sent[0]["nachricht"]  # the approval itself
    assert svc.reminder_tick(sunday_evening) is True
    assert "Morgen gibt's" in sent[1]["nachricht"] and "Tiefkühlware" in sent[1]["nachricht"]
    assert svc.reminder_tick(sunday_evening) is False  # once a day


def test_week_hero_names_the_next_dish_and_tomorrows_preparation(svc):
    from tischlein.views import week_view

    plan = svc.generate(MONDAY)
    monday, tuesday = plan.meals[0], plan.meals[1]
    svc.fake.recipes[tuesday.recipe_uid]["ingredients"] += "\n500 g TK Erbsen"
    svc.fake.recipes[tuesday.recipe_uid]["hash"] = "geaendert"
    svc.sync_paprika()

    hero = week_view(svc, None, MONDAY)["hero"]
    assert hero["headline"] == f"Heute gibt’s {monday.name}."
    assert hero["tomorrow"] == tuesday.name and any("Tiefkühl" in p for p in hero["prep"])

    sunday = week_view(svc, None, MONDAY - timedelta(days=1))["hero"]
    assert sunday["headline"] == f"Morgen gibt’s {monday.name}." and sunday["tomorrow"] is None

    monday_eve = week_view(svc, None, MONDAY)["hero"]  # prep is for Tuesday, named because the hero is Monday
    assert monday_eve["tomorrow"] == tuesday.name

    far = week_view(svc, None, MONDAY - timedelta(days=7))["hero"]  # only next week is planned
    assert far["headline"].startswith("Am Montag, 16. November gibt’s")

    empty = week_view(svc, None, MONDAY + timedelta(days=30))["hero"]
    assert empty["meal"] is None


def test_dislikes_quick_days_and_cooking_view(svc):
    from tischlein.views import cook_view

    svc.fake.recipes["R2"].update(ingredients="1 kg Kartoffeln\n200 g Steinpilze", hash="pilz")
    svc.fake.recipes["R1"].update(total_time="20 Min.", hash="schnell")
    svc.sync_paprika()
    svc.save_dislikes("Pilze, Pilze,\nOliven, Ei")
    assert svc.avoided_uids() == svc.avoided_uids()  # cached until the raw cache or the dislikes change
    assert svc.dislikes() == ["Pilze", "Oliven", "Ei"] and svc.avoided_uids() == {"R2"}  # Steinpilze
    svc.fake.recipes["R1"].update(ingredients="400 g Hähnchen\n2 EL Olivenöl\neine Zwiebel\nEis", hash="oel")
    svc.sync_paprika()
    assert svc.avoided_uids() == {"R2"}  # no "Oliven" in Olivenöl, no "Ei" in "eine" or "Eis"

    svc.set_quick(MONDAY, True)  # only the 20-minute Bowl is quick
    for seed in range(5):
        svc.rng.seed(seed)
        assert svc.generate(MONDAY).meals[0].name == "Bowl"
    assert svc.store.quickdays(MONDAY, MONDAY + timedelta(days=6)) == {MONDAY}
    tuesday = MONDAY + timedelta(days=1)
    svc.set_quick(tuesday, True)  # nothing quick left for Tuesday (the Bowl is taken): Püree stays
    assert next(m for m in svc.store.plan(MONDAY).meals if m.day == tuesday).name == "Püree"
    assert tuesday in svc.store.quickdays(MONDAY, MONDAY + timedelta(days=6))

    svc.set_servings(tuesday, 8)
    v = cook_view(svc, tuesday)
    assert v["servings"] == 8 and v["scaled"] and v["ingredients"][0]["text"] == "2 kg Kartoffeln"  # Püree, doubled
    with pytest.raises(KeyError):
        cook_view(svc, MONDAY + timedelta(days=5))


def test_people_names(svc):
    assert svc.person_name("julia@example.com") == "julia@example.com"
    svc.save_people({"julia@example.com": "Julia", "fremd@example.com": "X"})
    assert svc.person_name("julia@example.com") == "Julia"
    assert "fremd@example.com" not in svc.people()


def test_bring_push_is_remembered_only_when_home_assistant_has_it(svc, monkeypatch):
    svc.webhook_url = "http://ha/hook"
    svc.set_bring_sync(True)
    monkeypatch.setattr(svc, "_post_async", lambda p, ok=None: None)  # Home Assistant never answers
    svc.add_shopping_item(MONDAY, "Milch", "")
    assert svc.store.shopping(MONDAY)[0]["bring"] == 0
    assert svc.apply_bring_state("", [{"summary": "Brot", "status": "needs_action"}], today=MONDAY)["abgehakt"] == 0
    assert not {i["name"]: i for i in svc.store.shopping(MONDAY)}["Milch"]["done"]  # not "bought" by mistake


def test_regenerating_the_list_keeps_ticks_and_bring_state(svc, monkeypatch):
    sent = []
    monkeypatch.setattr(svc, "_post_async", lambda p, ok=None: (sent.append(p), ok and ok()))
    svc.webhook_url = "http://ha/hook"
    svc.set_bring_sync(True)
    svc.generate(MONDAY)
    svc.generate_shopping(MONDAY)
    rows = {i["name"]: i for i in svc.store.shopping(MONDAY)}
    svc.set_item_done(rows["Zwiebel"]["id"], True, "Julia")
    n = len(sent)
    svc.generate_shopping(MONDAY)  # again, same plan: nothing new for Bring, the tick stays
    rows = {i["name"]: i for i in svc.store.shopping(MONDAY)}
    assert rows["Zwiebel"]["done"] and rows["Zwiebel"]["bring"] == 2
    assert all(not p["offen"] and not p["erledigt"] for p in sent[n:] if p["art"] == "bring_sync")


def test_a_deleted_ingredient_stays_off_when_collecting_again(svc, monkeypatch):
    sent = []
    monkeypatch.setattr(svc, "_post_async", lambda p, ok=None: (sent.append(p), ok and ok()))
    svc.webhook_url = "http://ha/hook"
    svc.set_bring_sync(True)
    svc.generate(MONDAY)
    svc.generate_shopping(MONDAY)
    rows = {i["name"]: i for i in svc.store.shopping(MONDAY)}
    svc.delete_item(rows["Zwiebel"]["id"])
    assert "Zwiebel" in sent[-1]["erledigt"]  # it leaves Bring too
    # Bring reports its list before it got the removal: Zwiebel does not come back as "Aus Bring"
    svc.apply_bring_state("", [{"summary": "Zwiebel", "status": "needs_action"}], today=MONDAY)
    svc.generate_shopping(MONDAY)
    assert "Zwiebel" not in {i["name"] for i in svc.store.shopping(MONDAY)}
    assert not svc.store.removed_shopping(MONDAY + timedelta(days=7))  # next week collects it again


def test_a_deleted_ingredient_comes_back_when_another_dish_needs_it():
    removed = {"zwiebel": "Chili"}
    assert service_mod._removed_by_hand(removed, {"name": "Zwiebeln", "recipes": "Chili"})
    assert not service_mod._removed_by_hand(removed, {"name": "Zwiebel", "recipes": "Chili, Gulasch"})
    assert not service_mod._removed_by_hand(removed, {"name": "Paprika", "recipes": "Chili"})


def test_removing_bought_items_keeps_them_off_when_collecting_again(svc):
    svc.generate(MONDAY)
    svc.generate_shopping(MONDAY)
    rows = {i["name"]: i for i in svc.store.shopping(MONDAY)}
    svc.set_item_done(rows["Zwiebel"]["id"], True)
    svc.remove_done(MONDAY)
    svc.generate_shopping(MONDAY)
    assert "Zwiebel" not in {i["name"] for i in svc.store.shopping(MONDAY)}
