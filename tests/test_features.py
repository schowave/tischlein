import json
import random
from datetime import date, datetime

import pytest
from test_tagging import WritablePaprika, client

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
    names = [i["name"] for i in svc.store.shopping(MONDAY)]
    assert "1 Zwiebel" in names and "Salz" not in names and "2 EL Öl" not in names

    svc.store.set_meta("llm_base_url", "u")
    svc.store.set_meta("llm_api_key", "k")
    svc.store.set_meta("llm_model", "m")
    fake = FakeLLM(
        [{"items": [{"name": "Zwiebeln", "amount": "2", "group": "Obst & Gemüse", "recipes": ["Bowl", "Püree"]}]}]
    )
    svc._llm_transport = fake
    assert svc.generate_shopping(MONDAY) == "llm"
    assert "1 Zwiebel  (für: Bowl)" in fake.calls[0]["messages"][1]["content"]
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
    created = svc.fake.recipes[recipe.uid]
    assert created["ingredients"] == "400 g Seelachs" and created["hash"]
    assert svc.store.idea(idea["id"])["status"] == "uebernommen"
    with pytest.raises(KeyError):
        svc.accept_idea(idea["id"])


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
    sunday_evening = datetime(2026, 11, 15, 18, 5)
    assert svc.reminder_tick(sunday_evening) is False  # plan is only a draft
    svc.set_status(MONDAY, "freigegeben")
    svc.store.set_meta("erinnert_am", "")
    assert svc.reminder_tick(datetime(2026, 11, 15, 17, 0)) is False  # too early
    assert svc.reminder_tick(sunday_evening) is True
    assert "Morgen gibt's" in sent[0]["nachricht"] and "Tiefkühlware" in sent[0]["nachricht"]
    assert svc.reminder_tick(sunday_evening) is False  # once a day


def test_people_names(svc):
    assert svc.person_name("julia@example.com") == "julia@example.com"
    svc.save_people({"julia@example.com": "Julia", "fremd@example.com": "X"})
    assert svc.person_name("julia@example.com") == "Julia"
    assert "fremd@example.com" not in svc.people()
