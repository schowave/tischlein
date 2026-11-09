"""SQLite persistence. The only module that contains SQL."""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path

from .models import Meal, Recipe, Verdict, WeekPlan

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
    uid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    tags TEXT NOT NULL,
    rating INTEGER NOT NULL DEFAULT 0,
    source_url TEXT NOT NULL DEFAULT '',
    total_time TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    week_start TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('entwurf', 'freigegeben')),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meals (
    week_start TEXT NOT NULL REFERENCES plans(week_start) ON DELETE CASCADE,
    day TEXT NOT NULL,
    recipe_uid TEXT,
    name TEXT NOT NULL,
    theme TEXT NOT NULL,
    is_new INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (week_start, day)
);
CREATE TABLE IF NOT EXISTS paprika_cache (
    uid TEXT PRIMARY KEY,
    hash TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    recipe_uid TEXT PRIMARY KEY,
    verdict TEXT NOT NULL CHECK (verdict IN ('ok', 'nein')),
    at TEXT NOT NULL
);
"""


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
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

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
                "INSERT INTO meals VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(ws, m.day.isoformat(), m.recipe_uid, m.name, m.theme, int(m.is_new), m.reason) for m in plan.meals],
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
            date.fromisoformat(m["day"]), m["recipe_uid"], m["name"], m["theme"], bool(m["is_new"]), m["reason"]
        )

    # Feedback --------------------------------------------------------------

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
