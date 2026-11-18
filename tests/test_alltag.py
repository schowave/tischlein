"""Saison im Plan, Kinder-OK aus Bewertungen, Meldungen, Wunschliste, Vorschläge und Stöbern."""

import json
import random
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tischlein import season
from tischlein.app import create_app
from tischlein.config import PlanConfig, parse_config
from tischlein.models import Recipe
from tischlein.service import Tischlein
from tischlein.store import Store
from tischlein.views import review_view, week_view

MONDAY = date(2026, 11, 16)
NEXT = MONDAY + timedelta(weeks=1)
DAY = [MONDAY + timedelta(days=i) for i in range(5)]
RECIPES = {
    "h1": ("Hähnchen-Bowl", ("Hähnchen", "Kinder-OK", "Hauptspeisen"), "400 g Hähnchen\n1 Zwiebel"),
    "h2": ("Lauch-Hähnchen", ("Hähnchen", "Hauptspeisen"), "500 g Hähnchen\n2 Stangen Lauch"),
    "h3": ("Spargel-Hähnchen", ("Hähnchen", "Kinder-OK", "Hauptspeisen"), "500 g Hähnchen\n500 g Spargel"),
    "f1": ("Fischstäbchen", ("Fisch", "Kinder-OK"), "1 Packung Fischstäbchen"),
    "l1": ("Linsen-Dal", ("Hülsenfrüchte", "Kinder-OK"), "250 g Linsen"),
    "p1": ("Spaghetti", ("Pasta/Reis", "Kinder-OK"), "500 g Spaghetti"),
    "e1": ("Pfannkuchen", ("Eier/Quark", "Kinder-OK"), "4 Eier\n500 ml Milch"),
    "x1": ("Gemüsepfanne", ("Hauptspeisen",), "1 Zucchini\n2 Paprika\n200 g Feta"),
}


class Hook:
    """Collects what would go to Home Assistant."""

    def __init__(self, svc):
        self.sent = []
        svc.webhook_url = "http://ha.invalid/hook"
        svc._post_webhook = lambda payload: self.sent.append(payload) or True


@pytest.fixture
def svc():
    store = Store(":memory:")
    for uid, (name, tags, ingredients) in RECIPES.items():
        store.upsert_recipe(Recipe(uid, name, tags), uid, {"uid": uid, "name": name, "ingredients": ingredients})
    return Tischlein(store, PlanConfig(new_slot=None, holiday_state="BW"), rng=random.Random(3))


def meals(plan):
    return {m.day: m for m in plan.meals}


# Saison ---------------------------------------------------------------------------


def test_season_weights_the_plan_but_never_rules_a_dish_out(svc):
    weights = svc.plan_weights(MONDAY)  # November
    assert weights["h2"] == season.IN_SEASON and weights["h3"] == season.OUT_OF_SEASON
    assert "h1" not in weights
    assert svc.plan_weights(date(2027, 5, 3))["h3"] == season.IN_SEASON  # asparagus in May
    svc.generate(MONDAY)
    assert meals(svc.choose(DAY[0], "h3", today=MONDAY))[DAY[0]].recipe_uid == "h3"  # by hand always works


def test_season_combines_with_the_ratings(svc):
    svc.rate("h2", MONDAY - timedelta(days=7), "gut")
    assert svc.plan_weights(MONDAY)["h2"] == pytest.approx(1.6 * season.IN_SEASON)


def test_week_shows_the_season_and_hides_the_theme_next_to_own_choices(svc):
    svc.generate(MONDAY)
    svc.choose(DAY[0], "h2", today=MONDAY)
    days = {d["date"]: d for d in week_view(svc, "aktuell", MONDAY)["days"]}
    assert days[DAY[0]]["season"] == "Lauch" and not days[DAY[0]]["show_theme"]
    assert days[DAY[2]]["show_theme"] and days[DAY[2]]["season"] == ""


# Kinder-OK aus Bewertungen ------------------------------------------------------------


def test_first_gut_makes_a_dish_kinder_ok_and_can_be_taken_back(svc):
    assert svc.rate("h2", MONDAY, "gut") is True
    assert svc.store.feedback()["h2"] == "ok"
    assert svc.rate("h2", MONDAY + timedelta(days=7), "gut") is False  # only the first time
    svc.unlearn_kid_ok("h2")
    assert "h2" not in svc.store.feedback()
    with pytest.raises(ValueError):
        svc.unlearn_kid_ok("h2")
    assert svc.rate("h2", MONDAY + timedelta(days=14), "gut") is False  # the family's no stands
    assert "h2" not in svc.store.feedback()


def test_gut_after_an_earlier_rating_teaches_nothing(svc):
    svc.rate("h2", MONDAY, "schlecht")
    assert svc.rate("h2", MONDAY + timedelta(days=7), "gut") is False
    assert "h2" not in svc.store.feedback()


def test_a_neu_dish_joins_the_rotation_as_before_without_the_announcement(svc):
    svc.store.upsert_recipe(Recipe("n1", "Shakshuka", ("Eier/Quark", "Neu")), "n", {})
    assert svc.rate("n1", MONDAY, "gut") is False
    assert svc.store.feedback()["n1"] == "ok"
    with pytest.raises(ValueError):
        svc.unlearn_kid_ok("n1")


def test_gut_for_a_kinder_ok_dish_and_ging_so_change_nothing(svc):
    assert svc.rate("h1", MONDAY, "gut") is False
    assert svc.rate("h2", MONDAY, "mittel") is False
    assert svc.store.feedback() == {}


def test_rating_page_says_kinder_ok_with_a_way_back(svc):
    client = TestClient(create_app(svc, auto_sync=False), follow_redirects=False)
    r = client.post("/bewertung", data={"rezept_uid": "h2", "tag": MONDAY.isoformat(), "urteil": "gut"})
    assert "gelernt=h2" in r.headers["location"]
    page = client.get(r.headers["location"]).text
    assert "Lauch-Hähnchen ist jetzt Kinder-OK" in page and "/rezepte/h2/kinder-ok-zurueck" in page
    assert client.post("/rezepte/h2/kinder-ok-zurueck").status_code == 303
    assert "h2" not in svc.store.feedback()


# Meldungen --------------------------------------------------------------------------


def test_wie_wars_once_after_lunch_and_only_without_a_rating(svc):
    hook = Hook(svc)
    svc.generate(MONDAY)
    svc.set_status(MONDAY, "freigegeben")
    hook.sent.clear()
    assert svc.rating_tick(datetime(2026, 11, 16, 12, 30)) is False  # before 13:00
    assert svc.rating_tick(datetime(2026, 11, 16, 13, 5)) is True
    assert hook.sent[0]["art"] == "bewertung" and hook.sent[0]["datum"] == "2026-11-16"
    assert svc.rating_tick(datetime(2026, 11, 16, 14, 0)) is False  # once a day
    dish = meals(svc.store.plan(MONDAY))[DAY[1]]
    svc.rate(dish.recipe_uid, DAY[1], "gut")
    assert svc.rating_tick(datetime(2026, 11, 17, 13, 5)) is False  # already rated


def test_messages_are_tried_again_when_home_assistant_is_away(svc):
    hook = Hook(svc)
    svc.generate(MONDAY, today=MONDAY)
    svc.set_status(MONDAY, "freigegeben")
    svc._post_webhook = lambda payload: False
    assert svc.rating_tick(datetime(2026, 11, 16, 13, 0)) is False
    assert svc.draft_tick(datetime(2026, 11, 20, 17, 0)) is False
    plan = svc.store.plan(NEXT)
    assert plan is not None  # the draft is there, only the message is owed
    svc._post_webhook = lambda payload: hook.sent.append(payload) or True
    hook.sent.clear()
    assert svc.rating_tick(datetime(2026, 11, 16, 13, 1)) is True
    assert svc.draft_tick(datetime(2026, 11, 20, 17, 1)) is True
    assert [p["art"] for p in hook.sent] == ["bewertung", "entwurf"]
    assert svc.store.plan(NEXT).meals == plan.meals
    assert svc.draft_tick(datetime(2026, 11, 20, 17, 2)) is False


def test_wie_wars_not_for_drafts_free_days_and_leftovers(svc):
    hook = Hook(svc)
    svc.generate(MONDAY)
    assert svc.rating_tick(datetime(2026, 11, 16, 13, 5)) is False  # a draft was not cooked
    svc.set_status(MONDAY, "freigegeben")
    svc.set_leftovers(DAY[1], True)
    svc.set_day_free(DAY[3], "Urlaub")
    assert svc.rating_tick(datetime(2026, 11, 18, 13, 5)) is False
    assert svc.rating_tick(datetime(2026, 11, 19, 13, 5)) is False
    assert not [p for p in hook.sent if p.get("art") == "bewertung"]


def test_answer_from_the_notification_rates_what_was_eaten(svc):
    svc.generate(MONDAY)
    svc.set_status(MONDAY, "freigegeben")
    svc.log_cooked(DAY[0], "h2", today=DAY[0])
    client = TestClient(create_app(svc, auto_sync=False))
    r = client.post("/api/bewertung", json={"datum": DAY[0].isoformat(), "urteil": "gut", "von": "Julia"})
    assert r.json() == {"ok": True, "gericht": "Lauch-Hähnchen"}
    assert svc.store.ratings()[0]["uid"] == "h2" and svc.store.feedback()["h2"] == "ok"
    assert client.post("/api/bewertung", json={"datum": "2026-11-21", "urteil": "gut"}).status_code == 404
    assert client.post("/api/bewertung", json={"datum": DAY[1].isoformat(), "urteil": "super"}).status_code == 400


def test_draft_for_next_week_comes_by_itself_and_is_never_approved(svc):
    hook = Hook(svc)
    assert svc.draft_tick(datetime(2026, 11, 20, 16, 0)) is False  # Friday before 17:00
    assert svc.draft_tick(datetime(2026, 11, 20, 17, 0)) is True
    plan = svc.store.plan(NEXT)
    assert plan.status == "entwurf" and hook.sent[-1]["art"] == "entwurf" and hook.sent[-1]["woche"] == "2026-11-23"
    assert "Mo " in hook.sent[-1]["nachricht"]
    assert svc.draft_tick(datetime(2026, 11, 21, 9, 0)) is False  # once per week
    assert svc.store.plan(NEXT).meals == plan.meals


def test_draft_tick_keeps_an_existing_plan_and_catches_up_after_downtime(svc):
    hook = Hook(svc)
    mine = svc.generate(NEXT)
    hook.sent.clear()
    assert svc.draft_tick(datetime(2026, 11, 21, 8, 0)) is True  # server was down on Friday
    assert svc.store.plan(NEXT).meals == mine.meals and len(hook.sent) == 1


def test_reminder_when_next_week_still_waits_for_approval(svc):
    hook = Hook(svc)
    svc.generate(NEXT)
    hook.sent.clear()
    assert svc.approve_tick(datetime(2026, 11, 22, 17, 0)) is False  # Sunday before 18:00
    assert svc.approve_tick(datetime(2026, 11, 22, 18, 0)) is True
    assert "noch nicht freigegeben" in hook.sent[0]["nachricht"]
    assert svc.approve_tick(datetime(2026, 11, 22, 19, 0)) is False


def test_no_reminder_for_an_approved_week_and_approval_from_the_notification(svc):
    hook = Hook(svc)
    svc.generate(NEXT)
    client = TestClient(create_app(svc, auto_sync=False))
    hook.sent.clear()
    r = client.post("/api/plaene/freigeben", json={"woche": "2026-11-23", "von": "David"})
    assert r.json()["status"] == "freigegeben"
    assert not hook.sent  # no "… hat freigegeben" to everyone: the tap clears the notification
    assert svc.approve_tick(datetime(2026, 11, 22, 18, 0)) is False and not hook.sent
    assert client.post("/api/plaene/freigeben", json={"woche": "2026-12-07"}).status_code == 404


def test_message_times_come_from_the_config():
    cfg = parse_config({"erinnerung": {"bewertung": "", "entwurf": "sa 9:30", "freigabe": ""}})
    assert cfg.rating_time == "" and cfg.draft_time == (5, "09:30") and cfg.approve_time is None
    assert parse_config({}).draft_time == (4, "17:00")
    with pytest.raises(ValueError):
        parse_config({"erinnerung": {"entwurf": "fr"}})


# Wunschliste ------------------------------------------------------------------------


def test_a_wish_lands_on_its_theme_day_in_the_next_draft(svc):
    assert "nächsten Wochenentwurf" in svc.add_wish("h2", today=DAY[2])
    assert [r.uid for r in svc.wishes()] == ["h2"]
    plan = meals(svc.generate(NEXT, today=DAY[2]))
    monday = plan[NEXT]
    assert monday.recipe_uid == "h2" and monday.chosen and monday.reason == "Gewünscht"
    assert svc.wishes() == []
    assert meals(svc.generate(NEXT, today=DAY[2]))[NEXT].recipe_uid == "h2"  # stays when rolled again


def test_a_wish_without_a_theme_takes_the_first_open_day_and_skips_free_days(svc):
    svc.store.set_skipday(NEXT, "Urlaub")
    svc.add_wish("x1", today=DAY[2])
    plan = meals(svc.generate(NEXT, today=DAY[2]))
    assert plan[NEXT].skipped and plan[NEXT + timedelta(days=1)].recipe_uid == "x1"


def test_a_wish_goes_straight_into_an_existing_draft(svc):
    svc.generate(NEXT, today=DAY[2])
    text = svc.add_wish("h2", today=DAY[2])
    assert "steht jetzt am Montag" in text and svc.wishes() == []
    assert meals(svc.store.plan(NEXT))[NEXT].recipe_uid == "h2"
    assert "steht schon am Montag" in svc.add_wish("h2", today=DAY[2])


def test_a_wish_waits_when_next_week_is_approved_and_is_not_used_for_the_running_week(svc):
    svc.generate(NEXT, today=DAY[2])
    svc.set_status(NEXT, "freigegeben")
    assert "schon freigegeben" in svc.add_wish("h2", today=DAY[2])
    svc.generate(MONDAY, today=DAY[2])  # the running week leaves the wish alone
    assert [r.uid for r in svc.wishes()] == ["h2"]
    later = NEXT + timedelta(weeks=1)
    assert meals(svc.generate(later, today=DAY[2]))[later].recipe_uid == "h2"


def test_undoing_the_plan_puts_a_used_wish_back(svc):
    svc.add_wish("h2", today=DAY[2])
    svc.generate(NEXT, today=DAY[2])
    assert svc.wishes() == []
    svc.undo(NEXT)
    assert svc.store.plan(NEXT) is None and [r.uid for r in svc.wishes()] == ["h2"]


def test_no_wish_for_a_dish_marked_nie_wieder(svc):
    svc.rate("h2", MONDAY, "nie")
    with pytest.raises(ValueError):
        svc.add_wish("h2", today=DAY[2])


def test_wish_api_and_week_page(svc):
    client = TestClient(create_app(svc, auto_sync=False))
    r = client.post("/api/rezepte/h2/wunsch", json={"an": True})
    assert r.json()["gewuenscht"] is True
    assert "Gewünscht für den nächsten Entwurf" in client.get("/").text
    assert client.post("/api/rezepte/h2/wunsch", json={"an": False}).json()["gewuenscht"] is False
    assert client.post("/api/rezepte/gibtsnicht/wunsch", json={"an": True}).status_code == 404


# Vorschläge ---------------------------------------------------------------------------


class Answers:
    """LLM transport that answers each request with the next prepared JSON."""

    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, url, headers, body):
        self.prompts.append(json.loads(body)["messages"][1]["content"])
        answer = self.answers.pop(0)
        content = json.dumps(answer, ensure_ascii=False)
        return 200, json.dumps({"choices": [{"message": {"content": content}}]}).encode()


def with_llm(svc, answers):
    svc.store.set_meta("llm_base_url", "http://llm.invalid")
    svc.store.set_meta("llm_api_key", "k")
    svc.store.set_meta("llm_model", "m")
    svc._llm_transport = Answers(answers)
    return svc._llm_transport


def test_proposals_for_theme_and_time_are_kept_until_confirmed(svc):
    # all eight recipes lack a time, only x1 lacks a theme; nr follows the alphabet
    names = sorted(RECIPES.values(), key=lambda r: r[0].casefold())
    nr = [n[0] for n in names].index("Gemüsepfanne") + 1
    llm = with_llm(svc, [{"rezepte": [{"nr": nr, "thema": "eier/quark", "minuten": 25}, {"nr": 99, "minuten": 5}]}])
    assert svc.generate_proposals() == 2
    assert "Zucchini" in llm.prompts[0] and "Mögliche Themen" in llm.prompts[0]
    assert svc.open_proposals() == [{"uid": "x1", "name": "Gemüsepfanne", "thema": "Eier/Quark", "minuten": 25}]
    svc.answer_proposal("x1", "zeit", True)
    assert svc.store.cooktimes() == {"x1": 25}
    assert next(r for r in svc.planning_recipes() if r.uid == "x1").total_time == "25 Min."
    svc.answer_proposal("x1", "thema", False)  # declined: not asked again
    assert svc.open_proposals() == []
    assert all(r.uid != "x1" for r, _, _ in svc._proposal_candidates())
    with pytest.raises(KeyError):
        svc.answer_proposal("x1", "thema", True)


def test_no_fitting_theme_is_remembered_and_a_failing_llm_is_reported(svc):
    names = [n[0] for n in sorted(RECIPES.values(), key=lambda r: r[0].casefold())]
    with_llm(svc, [{"rezepte": [{"nr": names.index("Gemüsepfanne") + 1, "thema": "", "minuten": None}]}])
    assert svc.generate_proposals() == 0
    kept = {(p["kind"], p["state"]) for p in svc.store.proposals(None)}
    assert kept == {("thema", "abgelehnt"), ("zeit", "abgelehnt")}
    assert all(r.uid != "x1" for r, _, _ in svc._proposal_candidates())  # not asked again
    svc._llm_transport = lambda *a: (500, b"kaputt")
    from tischlein.llm import LLMError

    with pytest.raises(LLMError):
        svc.generate_proposals()


def test_a_theme_set_by_hand_drops_the_open_proposal(svc):
    svc.store.add_proposals([("x1", "thema", "Fisch")])
    assert svc.open_proposals()[0]["thema"] == "Fisch"
    svc.store.upsert_recipe(Recipe("x1", "Gemüsepfanne", ("Hauptspeisen", "Eier/Quark")), "x", {})
    assert svc.open_proposals() == []
    with pytest.raises(KeyError):
        svc.answer_proposal("x1", "thema", True)


def test_confirmed_time_makes_a_dish_quick(svc):
    svc.store.set_cooktime("h1", 20)
    from tischlein.planner import is_quick

    quick = {r.uid for r in svc.planning_recipes() if is_quick(r)}
    assert quick == {"h1"}


# Stöbern ------------------------------------------------------------------------------

HITS = {
    "count": 3,
    "results": [
        {
            "recipe": {
                "title": "Lauchgemüse",
                "siteUrl": "https://www.chefkoch.de/rezepte/1/Lauchgemuese.html",
                "rating": {"rating": 4.71, "numVotes": 220},
                "preparationTime": 10,
                "hasImage": True,
                "previewImageUrlTemplate": "https://img.chefkoch-cdn.de/rezepte/1/bilder/2/<format>/l.jpg",
            }
        },
        {"recipe": {"title": "Nur mit Abo", "siteUrl": "https://www.chefkoch.de/rezepte/2/Abo.html", "isPlus": True}},
        {"recipe": {"title": "Fremde Seite", "siteUrl": "https://example.org/x"}},
    ],
}


def test_browsing_chefkoch_shows_free_hits_and_what_is_already_in_paprika(svc):
    asked = []
    svc.web_get = lambda url, size, types=(): (asked.append(url), (json.dumps(HITS).encode(), "application/json"))[1]
    hits = svc.search_web("  lauch   suppe ")
    assert "query=lauch+suppe" in asked[0] and "minimumRating=4" in asked[0]
    assert [h["name"] for h in hits] == ["Lauchgemüse"]
    assert hits[0]["stars"] == 4.7 and hits[0]["minutes"] == 10 and hits[0]["uid"] == ""
    assert hits[0]["image"].endswith("/crop-360x240/l.jpg")
    source = "https://www.chefkoch.de/rezepte/1/Lauchgemuese.html?utm_source=x"
    svc.store.upsert_recipe(Recipe("w1", "Lauchgemüse", (), source_url=source), "w", {})
    assert svc.search_web("lauch")[0]["uid"] == "w1"
    assert svc.search_web("   ") == []


def test_browse_page_says_when_the_search_is_down_and_offers_the_season(svc):
    def down(url, size, types=()):
        raise ValueError("Die Seite antwortet mit Fehler 503.")

    svc.web_get = down
    client = TestClient(create_app(svc, auto_sync=False))
    page = client.get("/ideen?suche=lauch").text
    assert "antwortet gerade nicht" in page and "Rezept-Link" in page
    assert "Jetzt Saison" in client.get("/ideen").text


# Aufräumen ------------------------------------------------------------------------------


def test_review_leaves_out_weeks_in_which_nothing_was_cooked(svc):
    for d in DAY:
        svc.store.set_skipday(d - timedelta(weeks=1), "Reste vom Vortag")
    svc.generate(MONDAY - timedelta(weeks=1))
    svc.set_status(MONDAY - timedelta(weeks=1), "freigegeben")
    svc.generate(MONDAY)
    svc.set_status(MONDAY, "freigegeben")
    v = review_view(svc, DAY[2], 8)
    assert len(v["week_list"]) == 1 and v["cooked"] == 3


def test_api_plan_shows_what_was_cooked_instead(svc):
    svc.generate(MONDAY)
    svc.log_cooked(DAY[0], "h2", today=DAY[0])
    client = TestClient(create_app(svc, auto_sync=False))
    monday = client.get("/api/plan?woche=2026-11-16").json()["mahlzeiten"][0]
    assert monday["gericht"] == "Lauch-Hähnchen"


def test_navigation_has_three_areas_and_the_review_hangs_under_the_week(svc):
    svc.generate(MONDAY)
    client = TestClient(create_app(svc, auto_sync=False))
    page = client.get("/?woche=2026-11-16").text
    tabbar = page.split('<nav class="tabbar"')[1]
    assert "Einkauf" in tabbar and "Rezepte" in tabbar and "Ideen" not in tabbar and "Rückblick" not in tabbar
    assert 'href="/rueckblick"' in page.split('<nav class="tabbar"')[0]
    assert "Reste vom Vortag" in page


def test_days_can_be_dragged_and_the_menu_starts_with_verschieben(svc):
    svc.generate(NEXT, today=DAY[2])
    client = TestClient(create_app(svc, auto_sync=False))
    page = client.get("/?woche=2026-11-23").text
    monday = page.split('id="tag-2026-11-23"')[1].split("</article>")[0]
    assert 'class="day-grip"' in monday and 'data-targets="2026-11-24,2026-11-25,2026-11-26,2026-11-27"' in monday
    assert monday.index("Verschieben auf") < monday.index("Anderes Gericht")
    assert "/static/ziehen.js" in page
