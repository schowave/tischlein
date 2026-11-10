import json

import pytest
from test_tagging import WritablePaprika, client

from tischlein.config import DayRule, PlanConfig
from tischlein.paprika_sync import PaprikaError
from tischlein.service import Tischlein
from tischlein.store import Store


@pytest.fixture
def svc():
    fake = WritablePaprika()
    cfg = PlanConfig(days=(DayRule(0, "Hähnchen"),), extra_themes=("Fleisch",), cooldown_weeks=2)
    s = Tischlein(Store(":memory:"), cfg, paprika=client(fake))
    s.sync_paprika()
    s.fake = fake
    return s


def test_sync_remembers_paprika_categories(svc):
    assert json.loads(svc.store.meta("paprika_categories")) == ["Beilage", "Hauptspeisen"]
    assert svc.categories() == ["Beilage", "Fleisch", "Hauptspeisen", "Hähnchen", "Kinder-OK", "Neu"]


def test_edit_tags_writes_to_paprika_and_refreshes_locally(svc):
    r = svc.edit_recipe_tags("R1", add=["Kinder-OK", "Hähnchen"], remove=["Hauptspeisen"])

    assert r.tags == ("Kinder-OK", "Hähnchen")  # unknown user category UID has no name and is dropped locally
    assert "C-USER" in svc.fake.recipes["R1"]["categories"]  # ...but kept in Paprika
    assert svc.store.recipe("R1").tags == ("Kinder-OK", "Hähnchen")
    assert "Kinder-OK" in json.loads(svc.store.meta("paprika_categories"))
    assert svc.store.paprika_cache()["R1"][1]["categories"] == svc.fake.recipes["R1"]["categories"]

    r = svc.edit_recipe_tags("R1", add=[], remove=["Kinder-OK"])
    assert r.tags == ("Hähnchen",)


def test_edit_tags_errors(svc):
    with pytest.raises(KeyError):
        svc.edit_recipe_tags("NOPE", add=["X"], remove=[])
    svc.paprika = None
    with pytest.raises(PaprikaError):
        svc.edit_recipe_tags("R1", add=["X"], remove=[])


def test_theme_supply_counts_kid_ok_against_rotation_need(svc):
    svc.edit_recipe_tags("R1", add=["Hähnchen", "Kinder-OK"], remove=[])
    svc.edit_recipe_tags("R2", add=["Hähnchen"], remove=[])
    assert svc.theme_supply() == [
        {"thema": "Hähnchen", "rezepte": 2, "kinder_ok": 1, "bedarf": 3, "hat_tag": True},
        {"thema": "Fleisch", "rezepte": 0, "kinder_ok": 0, "bedarf": 3, "hat_tag": False},
    ]
    svc.store.set_feedback("R2", "ok")
    assert svc.theme_supply()[0]["kinder_ok"] == 2


def test_recipe_details_parse_ingredients(svc):
    svc.store.set_raw_recipe(
        "R1",
        "x",
        {
            "uid": "R1",
            "ingredients": "**Sauce**\n200 g Sahne\n\n=====\nTeig:\n2 **Eier**",
            "servings": "4 ",
            "total_time": "45 Min.",
        },
    )
    d = svc.recipe_details("R1")
    assert d["zutaten"] == [
        {"text": "Sauce", "kopf": True},
        {"text": "200 g Sahne", "kopf": False},
        {"text": "Teig", "kopf": True},
        {"text": "2 Eier", "kopf": False},
    ]
    assert d["portionen"] == "4" and d["zeit"] == "45 Min." and d["bild"] is False
    with pytest.raises(KeyError):
        svc.recipe_details("NOPE")


def test_recipe_photo_caches_and_refreshes_expired_url(tmp_path, svc):
    svc.backup_dir = tmp_path
    svc.store.set_raw_recipe("R2", "y", {**svc.fake.recipes["R2"], "photo_url": "http://old"})
    svc.fake.recipes["R2"]["photo_url"] = "http://fresh"
    calls = []

    def fetch(url):
        calls.append(url)
        if url == "http://old":
            raise OSError("403 expired")
        return b"JPEG"

    p = svc.recipe_photo("R2", fetch=fetch)
    assert p.read_bytes() == b"JPEG" and calls == ["http://old", "http://fresh"]
    assert svc.store.raw_recipe("R2")[1]["photo_url"] == "http://fresh"
    assert svc.recipe_photo("R2", fetch=fetch) == p and len(calls) == 2  # served from disk
    assert svc.recipe_photo("R1", fetch=fetch) is None  # R1 has no photo


def test_trash_and_restore_recipe(svc):
    assert svc.set_recipe_trashed("R1", True) is None
    assert svc.fake.recipes["R1"]["in_trash"] is True
    assert svc.store.recipe("R1") is None
    assert [p for p, _ in svc.fake.posts][-1] == "notify/"
    svc.sync_paprika()  # a later sync keeps it out
    assert svc.store.recipe("R1") is None

    r = svc.set_recipe_trashed("R1", False)
    assert r.uid == "R1" and svc.fake.recipes["R1"]["in_trash"] is False
    assert svc.store.recipe("R1") is not None
    with pytest.raises(KeyError):
        svc.set_recipe_trashed("NOPE", True)


def test_background_sync_reports_progress_and_result(svc):
    svc.fake.recipes["R3"] = {"uid": "R3", "name": "Neu", "categories": [], "hash": "z"}
    seen = []
    original = svc.paprika.sync

    def spy(cache, progress=None):
        def record(phase, done, total):
            seen.append((phase, done, total))
            progress(phase, done, total)

        return original(cache, progress=record)

    svc.paprika.sync = spy
    state = svc.start_sync()
    assert state["laeuft"] is True
    assert svc.start_sync()["laeuft"] is True  # a second start joins the running sync
    svc._sync_thread.join(5)

    status = svc.sync_status()
    assert status["laeuft"] is False and status["fehler"] is None
    assert status["ergebnis"] == "3 Rezepte, 1 geladen, 0 entfernt"
    assert ("kategorien", 0, 0) in seen and ("rezepte", 0, 1) in seen and ("rezepte", 1, 1) in seen
    assert svc.store.recipe("R3") is not None


def test_background_sync_reports_errors(svc):
    def broken(cache, progress=None):
        raise PaprikaError("Paprika nicht erreichbar")

    svc.paprika.sync = broken
    svc.start_sync()
    svc._sync_thread.join(5)
    assert svc.sync_status()["fehler"] == "Paprika nicht erreichbar"
    assert "nicht erreichbar" in svc.store.meta("last_sync_error")


def test_auto_sync_only_runs_when_paprika_counters_change(svc):
    # first check after a manual sync: counters unknown -> sync once and remember them
    assert svc.auto_sync_tick() is True
    svc._sync_thread.join(5)
    assert json.loads(svc.store.meta("paprika_counters")) == {"categories": 3, "recipes": 10}

    assert svc.auto_sync_tick() is False  # nothing changed in Paprika
    svc.fake.counters["meals"] = 2
    assert svc.auto_sync_tick() is False  # a meal plan change does not matter to Tischlein

    svc.fake.counters["recipes"] = 11
    svc.fake.recipes["R9"] = {"uid": "R9", "name": "Neu", "categories": [], "hash": "n"}
    assert svc.auto_sync_tick() is True
    svc._sync_thread.join(5)
    assert svc.store.recipe("R9") is not None
    assert svc.store.meta("auto_check")


def test_auto_sync_tick_without_paprika_does_nothing():
    from tischlein.config import PlanConfig as PC

    s = Tischlein(Store(":memory:"), PC())
    assert s.auto_sync_tick() is False
