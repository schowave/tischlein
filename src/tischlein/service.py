"""Use cases shared by web app and CLI: glue between store, planner and config."""

from __future__ import annotations

import json
import random
import urllib.request
from dataclasses import replace
from datetime import date, timedelta

from .config import PlanConfig
from .models import Verdict, WeekPlan
from .planner import plan_week, week_start_of
from .store import Store


class Tischlein:
    def __init__(self, store: Store, config: PlanConfig, webhook_url: str = "", rng: random.Random | None = None):
        self.store = store
        self.config = config
        self.webhook_url = webhook_url
        self.rng = rng or random.Random()

    @staticmethod
    def resolve_week(which: str, today: date | None = None) -> date:
        """'aktuell', 'naechste' or an ISO date inside the wanted week."""
        today = today or date.today()
        if which == "aktuell":
            return week_start_of(today)
        if which == "naechste":
            return week_start_of(today) + timedelta(weeks=1)
        return week_start_of(date.fromisoformat(which))

    def generate(self, week_start: date, notify: bool = False) -> WeekPlan:
        meals = plan_week(
            week_start,
            self.store.recipes(),
            self.config,
            last_cooked=self.store.last_cooked(week_start),
            feedback=self.store.feedback(),
            rng=self.rng,
        )
        plan = WeekPlan(week_start, "entwurf", meals)
        self.store.save_plan(plan)
        if notify:
            self._notify(plan)
        return plan

    def swap(self, week_start: date, day: date) -> WeekPlan:
        plan = self._require(week_start)
        current = next((m for m in plan.meals if m.day == day), None)
        if current is None:
            raise KeyError(f"Kein Tag {day} im Plan")
        keep = {m.day: m for m in plan.meals if m.day != day}
        plan.meals = plan_week(
            week_start,
            self.store.recipes(),
            self.config,
            last_cooked=self.store.last_cooked(week_start),
            feedback=self.store.feedback(),
            rng=self.rng,
            keep=keep,
            exclude={current.recipe_uid} if current.recipe_uid else set(),
        )
        if current.recipe_uid:
            plan.meals = [
                replace(current, reason="Keine Alternative zum Thema vorhanden")
                if m.day == day and m.recipe_uid is None else m
                for m in plan.meals
            ]
        self.store.save_plan(plan)
        return plan

    def set_status(self, week_start: date, status: str) -> WeekPlan:
        plan = self._require(week_start)
        plan.status = status  # type: ignore[assignment]
        self.store.save_plan(plan)
        return plan

    def feedback(self, recipe_uid: str, verdict: Verdict) -> None:
        self.store.set_feedback(recipe_uid, verdict)

    def _require(self, week_start: date) -> WeekPlan:
        plan = self.store.plan(week_start)
        if plan is None:
            raise KeyError(f"Kein Plan für die Woche ab {week_start}")
        return plan

    def _notify(self, plan: WeekPlan) -> None:
        if not self.webhook_url:
            return
        body = json.dumps({
            "titel": "Mahlzeit",
            "nachricht": f"Plan für die Woche ab {plan.week_start:%d.%m.} ist da",
            "woche": plan.week_start.isoformat(),
            "gerichte": [m.name for m in plan.meals],
        }).encode()
        req = urllib.request.Request(self.webhook_url, body, {"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5).close()
        except OSError:
            pass  # notification is best effort
