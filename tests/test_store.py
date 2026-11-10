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


def test_store_is_safe_under_concurrent_reads(tmp_path):
    import threading

    s = Store(tmp_path / "t.db")
    s.replace_recipes([Recipe(f"u{i}", f"R{i}") for i in range(50)])
    for i in range(50):
        s.set_raw_recipe(f"u{i}", "h", {"uid": f"u{i}", "n": i})
    errors = []

    def worker():
        try:
            for i in range(50):
                assert s.raw_recipe(f"u{i}")[1]["n"] == i
                assert len(s.recipes()) == 50
        except Exception as e:  # noqa: BLE001 - collect anything for the assertion below
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE plans (week_start TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL);"
        "CREATE TABLE meals (week_start TEXT NOT NULL, day TEXT NOT NULL, recipe_uid TEXT, name TEXT NOT NULL,"
        " theme TEXT NOT NULL, is_new INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',"
        " PRIMARY KEY (week_start, day));"
        "INSERT INTO plans VALUES ('2026-11-16', 'entwurf', 'x');"
        "INSERT INTO meals VALUES ('2026-11-16', '2026-11-16', 'a', 'Bowl', 'Hähnchen', 0, '');"
    )
    db.commit()
    db.close()
    s = Store(path)
    plan = s.plan(MONDAY)
    assert plan.meals[0].name == "Bowl" and plan.meals[0].skipped is False


def test_skipdays_ratings_shopping_ideas_roundtrip():
    s = Store(":memory:")
    s.set_skipday(MONDAY, "Urlaub")
    assert s.skipdays(MONDAY, MONDAY) == {MONDAY: "Urlaub"}
    s.remove_skipday(MONDAY)
    assert s.skipdays(MONDAY, MONDAY) == {}

    s.set_rating("a", MONDAY, "gut", "David")
    s.set_rating("a", MONDAY, "mittel", "Julia")
    assert s.ratings() == [{"uid": "a", "day": MONDAY, "verdict": "mittel", "person": "Julia"}]

    s.replace_generated_shopping(MONDAY, [{"name": "Lachs", "amount": "600 g", "group": "Fisch"}])
    manual = s.add_shopping_item(MONDAY, "Kaffee")
    s.set_shopping_done(manual, True, "David")
    s.replace_generated_shopping(MONDAY, [{"name": "Hähnchen", "amount": "400 g", "group": "Fleisch"}])
    items = s.shopping(MONDAY)
    assert [(i["name"], i["manual"], i["done"]) for i in items] == [("Hähnchen", False, False), ("Kaffee", True, True)]

    s.add_ideas([{"name": "Fischcurry"}])
    idea = s.ideas()[0]
    s.set_idea_status(idea["id"], "uebernommen", "UID1")
    assert s.idea(idea["id"])["paprika_uid"] == "UID1" and s.ideas() == []


def test_fresh_database_is_at_alembic_head(tmp_path):
    import sqlite3

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    Store(tmp_path / "neu.db")
    cfg = Config()
    cfg.set_main_option("script_location", "src/tischlein/migrations")
    head = ScriptDirectory.from_config(cfg).get_current_head()
    version = sqlite3.connect(tmp_path / "neu.db").execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert version == head
