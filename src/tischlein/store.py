"""SQLite persistence. The only module that contains SQL."""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from .models import Meal, Recipe, Verdict, WeekPlan


def _locked(method):
    """Serialise access: the web server calls the store from several threads at once."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self.cache_generation = 0  # bumped on every change of the raw recipe cache (for derived indexes)
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA foreign_keys = ON")
        self._migrate()
        self.db.row_factory = sqlite3.Row

    # Recipes ---------------------------------------------------------------

    @_locked
    def replace_recipes(self, recipes: Iterable[Recipe]) -> int:
        """Replace the recipe catalogue with a fresh Paprika import."""
        now = datetime.now().isoformat(timespec="seconds")
        rows = [
            (r.uid, r.name, json.dumps(r.tags, ensure_ascii=False), r.rating, r.source_url, r.total_time, now)
            for r in recipes
        ]
        with self.db:
            self.db.execute("DELETE FROM recipes")
            self.db.executemany("INSERT INTO recipes VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        return len(rows)

    @_locked
    def recipes(self) -> list[Recipe]:
        rows = self.db.execute("SELECT * FROM recipes ORDER BY name").fetchall()
        return [
            Recipe(r["uid"], r["name"], tuple(json.loads(r["tags"])), r["rating"], r["source_url"], r["total_time"])
            for r in rows
        ]

    @_locked
    def upsert_recipe(self, r: Recipe, raw_hash: str, raw: dict) -> None:
        """Replace one recipe (and its raw Paprika data) after an edit."""
        self.cache_generation += 1
        now = datetime.now().isoformat(timespec="seconds")
        with self.db:
            self.db.execute("DELETE FROM recipes WHERE uid = ?", (r.uid,))
            self.db.execute(
                "INSERT INTO recipes VALUES (?, ?, ?, ?, ?, ?, ?)",
                (r.uid, r.name, json.dumps(r.tags, ensure_ascii=False), r.rating, r.source_url, r.total_time, now),
            )
            self.db.execute(
                "INSERT INTO paprika_cache VALUES (?, ?, ?) ON CONFLICT(uid) DO UPDATE SET "
                "hash = excluded.hash, data = excluded.data",
                (r.uid, raw_hash, json.dumps(raw, ensure_ascii=False)),
            )

    @_locked
    def delete_recipe(self, uid: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM recipes WHERE uid = ?", (uid,))

    @_locked
    def recipe(self, uid: str) -> Recipe | None:
        return next((r for r in self.recipes() if r.uid == uid), None)

    @_locked
    def raw_recipe(self, uid: str) -> tuple[str, dict] | None:
        row = self.db.execute("SELECT hash, data FROM paprika_cache WHERE uid = ?", (uid,)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    @_locked
    def set_raw_recipe(self, uid: str, raw_hash: str, raw: dict) -> None:
        self.cache_generation += 1
        with self.db:
            self.db.execute(
                "INSERT INTO paprika_cache VALUES (?, ?, ?) ON CONFLICT(uid) DO UPDATE SET "
                "hash = excluded.hash, data = excluded.data",
                (uid, raw_hash, json.dumps(raw, ensure_ascii=False)),
            )

    @_locked
    def paprika_cache(self) -> dict[str, tuple[str, dict]]:
        rows = self.db.execute("SELECT uid, hash, data FROM paprika_cache").fetchall()
        return {uid: (h, json.loads(data)) for uid, h, data in rows}

    @_locked
    def replace_paprika_cache(self, cache: dict[str, tuple[str, dict]]) -> None:
        self.cache_generation += 1
        with self.db:
            self.db.execute("DELETE FROM paprika_cache")
            self.db.executemany(
                "INSERT INTO paprika_cache VALUES (?, ?, ?)",
                [(uid, h, json.dumps(data, ensure_ascii=False)) for uid, (h, data) in cache.items()],
            )

    @_locked
    def set_meta(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value)
            )

    @_locked
    def meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    # Plans -----------------------------------------------------------------

    @_locked
    def save_plan(self, plan: WeekPlan) -> None:
        ws = plan.week_start.isoformat()
        now = datetime.now().isoformat(timespec="seconds")
        with self.db:
            self.db.execute(
                "INSERT INTO plans VALUES (?, ?, ?) "
                "ON CONFLICT(week_start) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
                (ws, plan.status, now),
            )
            self.db.execute("DELETE FROM meals WHERE week_start = ?", (ws,))
            self.db.executemany(
                "INSERT INTO meals (week_start, day, recipe_uid, name, theme, is_new, reason, skipped) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (ws, m.day.isoformat(), m.recipe_uid, m.name, m.theme, int(m.is_new), m.reason, int(m.skipped))
                    for m in plan.meals
                ],
            )
            # portions belong to the dish on that day: a swapped or re-rolled day starts again
            self.db.execute(
                "DELETE FROM servings WHERE day BETWEEN ? AND ? AND NOT EXISTS (SELECT 1 FROM meals m WHERE "
                "m.week_start = ? AND m.day = servings.day AND m.recipe_uid = servings.recipe_uid AND m.skipped = 0)",
                (ws, (plan.week_start + timedelta(days=6)).isoformat(), ws),
            )

    @_locked
    def plan(self, week_start: date) -> WeekPlan | None:
        row = self.db.execute("SELECT status FROM plans WHERE week_start = ?", (week_start.isoformat(),)).fetchone()
        if row is None:
            return None
        meals = self.db.execute(
            "SELECT * FROM meals WHERE week_start = ? ORDER BY day", (week_start.isoformat(),)
        ).fetchall()
        return WeekPlan(week_start, row["status"], [self._meal(m) for m in meals])

    @_locked
    def approved_plans(self) -> list[WeekPlan]:
        rows = self.db.execute("SELECT week_start FROM plans WHERE status = 'freigegeben' ORDER BY week_start")
        return [p for (ws,) in rows.fetchall() if (p := self.plan(date.fromisoformat(ws)))]

    @_locked
    def last_cooked(self, before: date) -> dict[str, date]:
        """Most recent day each recipe appeared in an approved plan before `before`."""
        rows = self.db.execute(
            "SELECT m.recipe_uid, MAX(m.day) FROM meals m JOIN plans p USING (week_start) "
            "WHERE p.status = 'freigegeben' AND m.recipe_uid IS NOT NULL AND m.day < ? GROUP BY m.recipe_uid",
            (before.isoformat(),),
        ).fetchall()
        return {uid: date.fromisoformat(day) for uid, day in rows}

    @staticmethod
    def _meal(m: sqlite3.Row) -> Meal:
        return Meal(
            date.fromisoformat(m["day"]),
            m["recipe_uid"],
            m["name"],
            m["theme"],
            bool(m["is_new"]),
            m["reason"],
            bool(m["skipped"]),
        )

    def _migrate(self) -> None:
        """Bring the schema to the latest Alembic revision (src/tischlein/migrations)."""
        engine = create_engine("sqlite://", creator=lambda: self.db, poolclass=StaticPool)
        cfg = AlembicConfig()
        cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            tables = {row[0] for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "alembic_version" not in tables and "recipes" in tables:
                command.stamp(cfg, "0001")  # database created before Alembic (up to 0.2.0)
            command.upgrade(cfg, "head")

    # Feedback --------------------------------------------------------------

    # Skip days ---------------------------------------------------------------

    @_locked
    def set_skipday(self, day: date, reason: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO skipdays VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET reason = excluded.reason",
                (day.isoformat(), reason),
            )

    @_locked
    def remove_skipday(self, day: date) -> None:
        with self.db:
            self.db.execute("DELETE FROM skipdays WHERE day = ?", (day.isoformat(),))

    @_locked
    def skipdays(self, start: date, end: date) -> dict[date, str]:
        rows = self.db.execute(
            "SELECT day, reason FROM skipdays WHERE day BETWEEN ? AND ?", (start.isoformat(), end.isoformat())
        ).fetchall()
        return {date.fromisoformat(d): r for d, r in rows}

    # Quick days --------------------------------------------------------------

    @_locked
    def set_quickday(self, day: date, on: bool) -> None:
        with self.db:
            if on:
                self.db.execute("INSERT OR IGNORE INTO quickdays VALUES (?)", (day.isoformat(),))
            else:
                self.db.execute("DELETE FROM quickdays WHERE day = ?", (day.isoformat(),))

    @_locked
    def quickdays(self, start: date, end: date) -> set[date]:
        rows = self.db.execute(
            "SELECT day FROM quickdays WHERE day BETWEEN ? AND ?", (start.isoformat(), end.isoformat())
        ).fetchall()
        return {date.fromisoformat(d) for (d,) in rows}

    # Portions ----------------------------------------------------------------

    @_locked
    def set_servings(self, day: date, recipe_uid: str, servings: int | None) -> None:
        """None removes the override, so the configured family portions apply again."""
        with self.db:
            if servings is None:
                self.db.execute("DELETE FROM servings WHERE day = ? AND recipe_uid = ?", (day.isoformat(), recipe_uid))
            else:
                self.db.execute(
                    "INSERT INTO servings VALUES (?, ?, ?) ON CONFLICT(day, recipe_uid) DO UPDATE SET "
                    "servings = excluded.servings",
                    (day.isoformat(), recipe_uid, servings),
                )

    @_locked
    def servings(self, start: date, end: date) -> dict[tuple[date, str], int]:
        rows = self.db.execute(
            "SELECT day, recipe_uid, servings FROM servings WHERE day BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return {(date.fromisoformat(d), u): n for d, u, n in rows}

    # Ratings -----------------------------------------------------------------

    @_locked
    def set_rating(self, recipe_uid: str, day: date, verdict: str, person: str = "") -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO ratings VALUES (?, ?, ?, ?, ?) ON CONFLICT(recipe_uid, day) DO UPDATE SET "
                "verdict = excluded.verdict, person = excluded.person, at = excluded.at",
                (recipe_uid, day.isoformat(), verdict, person, datetime.now().isoformat(timespec="seconds")),
            )

    @_locked
    def ratings(self) -> list[dict]:
        rows = self.db.execute("SELECT recipe_uid, day, verdict, person FROM ratings ORDER BY day").fetchall()
        return [{"uid": u, "day": date.fromisoformat(d), "verdict": v, "person": p} for u, d, v, p in rows]

    # Shopping list -----------------------------------------------------------

    @_locked
    def replace_generated_shopping(self, week_start: date, items: list[dict]) -> None:
        """Replace the generated items of a week; manual items and their state stay."""
        ws = week_start.isoformat()
        with self.db:
            self.db.execute("DELETE FROM shopping WHERE week_start = ? AND manual = 0", (ws,))
            self.db.executemany(
                "INSERT INTO shopping (week_start, name, amount, grp, recipes, manual, position) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                [
                    (ws, i["name"], i.get("amount", ""), i.get("group", "Sonstiges"), i.get("recipes", ""), n)
                    for n, i in enumerate(items)
                ],
            )

    @_locked
    def add_shopping_item(self, week_start: date, name: str, amount: str = "", group: str = "Sonstiges") -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO shopping (week_start, name, amount, grp, manual, position) VALUES (?, ?, ?, ?, 1, 9999)",
                (week_start.isoformat(), name, amount, group),
            )
        return cur.lastrowid

    @_locked
    def set_shopping_done(self, item_id: int, done: bool, person: str = "") -> bool:
        with self.db:
            cur = self.db.execute(
                "UPDATE shopping SET done = ?, done_by = ? WHERE id = ?", (int(done), person if done else "", item_id)
            )
        return cur.rowcount == 1

    @_locked
    def set_all_shopping_done(self, week_start: date, done: bool, person: str = "") -> int:
        with self.db:
            cur = self.db.execute(
                "UPDATE shopping SET done = ?, done_by = ? WHERE week_start = ? AND done != ?",
                (int(done), person if done else "", week_start.isoformat(), int(done)),
            )
        return cur.rowcount

    @_locked
    def delete_done_shopping(self, week_start: date) -> int:
        with self.db:
            cur = self.db.execute("DELETE FROM shopping WHERE week_start = ? AND done = 1", (week_start.isoformat(),))
        return cur.rowcount

    @_locked
    def delete_shopping_item(self, item_id: int) -> None:
        with self.db:
            self.db.execute("DELETE FROM shopping WHERE id = ?", (item_id,))

    @_locked
    def shopping(self, week_start: date) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, name, amount, grp, recipes, manual, done, done_by FROM shopping WHERE week_start = ? "
            "ORDER BY position, id",
            (week_start.isoformat(),),
        ).fetchall()
        return [
            {
                "id": i,
                "name": n,
                "amount": a,
                "group": g,
                "recipes": r,
                "manual": bool(m),
                "done": bool(d),
                "done_by": b,
            }
            for i, n, a, g, r, m, d, b in rows
        ]

    # Ideas -------------------------------------------------------------------

    @_locked
    def add_ideas(self, ideas: list[dict]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.db:
            self.db.executemany(
                "INSERT INTO ideas (created, data) VALUES (?, ?)",
                [(now, json.dumps(i, ensure_ascii=False)) for i in ideas],
            )

    @_locked
    def ideas(self, status: str = "neu") -> list[dict]:
        rows = self.db.execute(
            "SELECT id, created, data, status, paprika_uid FROM ideas WHERE status = ? ORDER BY id DESC", (status,)
        ).fetchall()
        return [{"id": i, "created": c, "status": s, "paprika_uid": p, **json.loads(d)} for i, c, d, s, p in rows]

    @_locked
    def idea(self, idea_id: int) -> dict | None:
        row = self.db.execute(
            "SELECT id, created, data, status, paprika_uid FROM ideas WHERE id = ?", (idea_id,)
        ).fetchone()
        if not row:
            return None
        i, c, d, s, p = row
        return {"id": i, "created": c, "status": s, "paprika_uid": p, **json.loads(d)}

    @_locked
    def set_idea_status(self, idea_id: int, status: str, paprika_uid: str = "") -> None:
        with self.db:
            self.db.execute("UPDATE ideas SET status = ?, paprika_uid = ? WHERE id = ?", (status, paprika_uid, idea_id))

    @_locked
    def set_feedback(self, recipe_uid: str, verdict: Verdict) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO feedback VALUES (?, ?, ?) "
                "ON CONFLICT(recipe_uid) DO UPDATE SET verdict = excluded.verdict, at = excluded.at",
                (recipe_uid, verdict, datetime.now().isoformat(timespec="seconds")),
            )

    @_locked
    def feedback(self) -> dict[str, Verdict]:
        return dict(self.db.execute("SELECT recipe_uid, verdict FROM feedback").fetchall())
