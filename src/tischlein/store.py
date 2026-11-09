"""SQLite persistence. The only module that contains SQL."""

from __future__ import annotations

import json
import sqlite3
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
CREATE TABLE IF NOT EXISTS feedback (
    recipe_uid TEXT PRIMARY KEY,
    verdict TEXT NOT NULL CHECK (verdict IN ('ok', 'nein')),
    at TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    # Recipes ---------------------------------------------------------------

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

    def recipes(self) -> list[Recipe]:
        rows = self.db.execute("SELECT * FROM recipes ORDER BY name").fetchall()
        return [
            Recipe(r["uid"], r["name"], tuple(json.loads(r["tags"])), r["rating"], r["source_url"], r["total_time"])
            for r in rows
        ]

    # Plans -----------------------------------------------------------------

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

    def plan(self, week_start: date) -> WeekPlan | None:
        row = self.db.execute("SELECT status FROM plans WHERE week_start = ?", (week_start.isoformat(),)).fetchone()
        if row is None:
            return None
        meals = self.db.execute(
            "SELECT * FROM meals WHERE week_start = ? ORDER BY day", (week_start.isoformat(),)
        ).fetchall()
        return WeekPlan(week_start, row["status"], [self._meal(m) for m in meals])

    def approved_plans(self) -> list[WeekPlan]:
        rows = self.db.execute("SELECT week_start FROM plans WHERE status = 'freigegeben' ORDER BY week_start")
        return [p for (ws,) in rows.fetchall() if (p := self.plan(date.fromisoformat(ws)))]

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
        return Meal(date.fromisoformat(m["day"]), m["recipe_uid"], m["name"], m["theme"], bool(m["is_new"]), m["reason"])

    # Feedback --------------------------------------------------------------

    def set_feedback(self, recipe_uid: str, verdict: Verdict) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO feedback VALUES (?, ?, ?) "
                "ON CONFLICT(recipe_uid) DO UPDATE SET verdict = excluded.verdict, at = excluded.at",
                (recipe_uid, verdict, datetime.now().isoformat(timespec="seconds")),
            )

    def feedback(self) -> dict[str, Verdict]:
        return dict(self.db.execute("SELECT recipe_uid, verdict FROM feedback").fetchall())
