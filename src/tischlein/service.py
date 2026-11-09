"""Use cases shared by web app and CLI: glue between store, planner and config."""

from __future__ import annotations

import json
import os
import random
import secrets
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from . import ics
from .config import PlanConfig
from .models import Recipe, Verdict, WeekPlan
from .paprika import recipe_from_json
from .paprika_sync import CachedRecipe, PaprikaClient, PaprikaError
from .planner import plan_week, week_start_of
from .store import Store
from .tagging import Change, apply_changes


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Tischlein/0.1"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        if not resp.headers.get("Content-Type", "").startswith("image/"):
            raise OSError("keine Bilddatei")
        return resp.read()


def from_env() -> Tischlein:
    """Build the service from environment variables (shared by web app and CLI)."""
    from .config import load_config
    from .paprika_sync import DEFAULT_USER_AGENT

    email, password = os.environ.get("PAPRIKA_EMAIL", ""), os.environ.get("PAPRIKA_PASSWORD", "")
    paprika = (
        PaprikaClient(email, password, user_agent=os.environ.get("PAPRIKA_USER_AGENT", DEFAULT_USER_AGENT))
        if email and password
        else None
    )
    return Tischlein(
        Store(os.environ.get("TISCHLEIN_DB", "data/tischlein.db")),
        load_config(Path(os.environ.get("TISCHLEIN_CONFIG", "tischlein.toml"))),
        webhook_url=os.environ.get("HA_WEBHOOK_URL", ""),
        paprika=paprika,
        user_agent=os.environ.get("PAPRIKA_USER_AGENT", DEFAULT_USER_AGENT),
    )


class Tischlein:
    def __init__(
        self,
        store: Store,
        config: PlanConfig,
        webhook_url: str = "",
        rng: random.Random | None = None,
        paprika: PaprikaClient | None = None,
        user_agent: str = "Tischlein/0.1",
    ):
        self.store = store
        self.config = config
        self.webhook_url = webhook_url
        self.rng = rng or random.Random()
        self.paprika = paprika
        self.backup_dir = Path(store.path).parent if store.path != ":memory:" else None
        self._paprika_lock = threading.Lock()
        self._photo_lock = threading.Lock()
        self._user_agent = user_agent

        # A login entered in the settings wins over PAPRIKA_EMAIL/PAPRIKA_PASSWORD.
        email, password = store.meta("paprika_email"), store.meta("paprika_password")
        if email and password:
            self.paprika = PaprikaClient(email, password, user_agent=user_agent)

    # Settings ----------------------------------------------------------------

    def paprika_account(self) -> dict:
        """Where the Paprika login comes from, without ever returning the password."""
        stored = self.store.meta("paprika_email")
        if stored and self.store.meta("paprika_password"):
            return {"verbunden": self.paprika is not None, "email": stored, "quelle": "einstellungen"}
        if self.paprika is not None:
            return {"verbunden": True, "email": self.paprika._email, "quelle": "umgebung"}
        return {"verbunden": False, "email": "", "quelle": None}

    def connect_paprika(self, email: str, password: str, client_factory=None) -> None:
        """Check the login against Paprika, then store it. An empty password keeps the stored one."""
        email = email.strip()
        if not password:
            if email != (self.store.meta("paprika_email") or ""):
                raise ValueError("Bitte das Passwort eingeben.")
            password = self.store.meta("paprika_password") or ""
        if not email or not password:
            raise ValueError("Bitte E-Mail und Passwort eingeben.")
        factory = client_factory or (lambda e, p: PaprikaClient(e, p, user_agent=self._user_agent))
        client = factory(email, password)
        client.login()  # raises PaprikaError with a German message
        self.store.set_meta("paprika_email", email)
        self.store.set_meta("paprika_password", password)
        self.paprika = client

    def disconnect_paprika(self) -> None:
        self.store.set_meta("paprika_email", "")
        self.store.set_meta("paprika_password", "")
        self.paprika = None

    def calendar_settings(self) -> dict:
        cfg = self.config
        token = self.store.meta("kalender_token")
        if not token:
            token = secrets.token_urlsafe(18)
            self.store.set_meta("kalender_token", token)
        return {
            "name": self.store.meta("kalender_name") or "Tischlein Mittagessen",
            "uhrzeit": self.store.meta("kalender_uhrzeit") or f"{cfg.lunch_time:%H:%M}",
            "minuten": int(self.store.meta("kalender_minuten") or cfg.lunch_minutes),
            "token": token,
        }

    def save_calendar_settings(self, name: str, uhrzeit: str, minuten: int) -> None:
        name = name.strip()[:60] or "Tischlein Mittagessen"
        try:
            hour, minute = (int(x) for x in uhrzeit.split(":"))
            dtime(hour, minute)
        except (ValueError, TypeError):
            raise ValueError("Uhrzeit bitte als HH:MM angeben, z. B. 12:15.") from None
        if not 10 <= minuten <= 240:
            raise ValueError("Die Dauer muss zwischen 10 und 240 Minuten liegen.")
        self.store.set_meta("kalender_name", name)
        self.store.set_meta("kalender_uhrzeit", f"{hour:02d}:{minute:02d}")
        self.store.set_meta("kalender_minuten", str(minuten))

    def renew_calendar_token(self) -> str:
        token = secrets.token_urlsafe(18)
        self.store.set_meta("kalender_token", token)
        return token

    def calendar_ics(self) -> str:
        s = self.calendar_settings()
        hour, minute = (int(x) for x in s["uhrzeit"].split(":"))
        cfg = replace(self.config, lunch_time=dtime(hour, minute), lunch_minutes=s["minuten"])
        return ics.render(self.store.approved_plans(), cfg, name=s["name"])

    # Recipes -----------------------------------------------------------------

    def categories(self) -> list[str]:
        """All category names known from Paprika plus the configured tags, sorted."""
        names = set(json.loads(self.store.meta("paprika_categories") or "[]"))
        names |= {t for r in self.store.recipes() for t in r.tags}
        names |= set(self.config.themes) | {self.config.kid_tag, self.config.new_tag}
        return sorted(names, key=str.casefold)

    def edit_recipe_tags(self, uid: str, add: list[str], remove: list[str]) -> Recipe:
        """Add/remove categories on one recipe in Paprika, then refresh it locally."""
        if self.paprika is None:
            raise PaprikaError("Paprika-Sync ist nicht eingerichtet (PAPRIKA_EMAIL/PAPRIKA_PASSWORD fehlen)")
        current = self.store.recipe(uid)
        if current is None:
            raise KeyError(f"Unbekanntes Rezept {uid}")
        add = [a.strip() for a in add if a.strip()]
        remove = [r.strip() for r in remove if r.strip()]
        backup = self.backup_dir / "paprika-backup-oberflaeche.jsonl" if self.backup_dir else None
        with self._paprika_lock:
            report = apply_changes(
                self.paprika,
                [Change(uid, current.name, tuple(add), tuple(remove))],
                execute=True,
                backup=backup,
                log=lambda _: None,
                delay=0,
            )
            if report.missing:
                raise KeyError(f"„{current.name}“ gibt es in Paprika nicht mehr")
            raw = self.paprika.recipe(uid)
            names = self.paprika.categories()
        recipe = recipe_from_json(raw, names)
        self.store.upsert_recipe(recipe, raw.get("hash", ""), raw)
        self.store.set_meta("paprika_categories", json.dumps(sorted(set(names.values())), ensure_ascii=False))
        return recipe

    # Photos and details --------------------------------------------------------

    def recipe_details(self, uid: str) -> dict:
        """Ingredients and facts for the detail view, from the raw Paprika data."""
        entry = self.store.raw_recipe(uid)
        if entry is None:
            raise KeyError(f"Unbekanntes Rezept {uid}")
        raw = entry[1]
        lines = []
        for line in (raw.get("ingredients") or "").splitlines():
            text = line.strip()
            if not text or set(text) <= set("=-*_ "):
                continue
            heading = (text.startswith("**") and text.rstrip(":").endswith("**")) or (
                text.endswith(":") and len(text) < 40
            )
            lines.append({"text": text.strip("*: ").strip() if heading else text.replace("**", ""), "kopf": heading})
        return {
            "uid": uid,
            "zutaten": lines,
            "portionen": (raw.get("servings") or "").strip(),
            "zeit": (raw.get("total_time") or raw.get("cook_time") or "").strip(),
            "quelle": raw.get("source_url") or "",
            "bild": bool(raw.get("photo_hash") or raw.get("photo")),
        }

    def recipe_photo(self, uid: str, fetch: Callable[[str], bytes] | None = None) -> Path | None:
        """Path to a locally cached photo; downloads it (refreshing the signed URL if needed)."""
        if self.backup_dir is None:
            return None
        entry = self.store.raw_recipe(uid)
        if entry is None:
            return None
        raw_hash, raw = entry
        key = (raw.get("photo_hash") or raw.get("photo") or "").replace("/", "")[:16]
        if not key:
            return None
        folder = self.backup_dir / "bilder"
        path = folder / f"{uid}-{key}.jpg"
        if path.exists():
            return path
        fetch = fetch or _download
        with self._photo_lock:
            if path.exists():
                return path
            data = None
            if raw.get("photo_url"):
                try:
                    data = fetch(raw["photo_url"])
                except OSError:
                    data = None
            if data is None and self.paprika is not None:
                time.sleep(0.25)  # Paprika blocks IPs on bursts
                try:
                    fresh = self.paprika.recipe(uid)
                except PaprikaError:
                    return None
                self.store.set_raw_recipe(uid, raw_hash, fresh)
                if fresh.get("photo_url"):
                    try:
                        data = fetch(fresh["photo_url"])
                    except OSError:
                        data = None
            if not data:
                return None
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.glob(f"{uid}-*.jpg"):
                old.unlink()
            path.write_bytes(data)
            return path

    def set_recipe_trashed(self, uid: str, trashed: bool) -> Recipe | None:
        """Move a recipe into Paprika's trash (or back). Reversible in Paprika and here."""
        if self.paprika is None:
            raise PaprikaError("Paprika-Sync ist nicht eingerichtet (PAPRIKA_EMAIL/PAPRIKA_PASSWORD fehlen)")
        if self.store.raw_recipe(uid) is None:
            raise KeyError(f"Unbekanntes Rezept {uid}")
        with self._paprika_lock:
            raw = self.paprika.recipe(uid)
            if self.backup_dir:
                with (self.backup_dir / "paprika-backup-oberflaeche.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(raw, ensure_ascii=False) + "\n")
            self.paprika.update_recipe({**raw, "in_trash": trashed})
            self.paprika.notify()
            fresh = self.paprika.recipe(uid)
            names = self.paprika.categories()
        if trashed:
            self.store.set_raw_recipe(uid, fresh.get("hash", ""), fresh)
            self.store.delete_recipe(uid)
            return None
        recipe = recipe_from_json(fresh, names)
        self.store.upsert_recipe(recipe, fresh.get("hash", ""), fresh)
        return recipe

    def theme_supply(self) -> list[dict]:
        """Per protein theme: how many recipes, how many Kinder-OK, and how many rotation needs."""
        recipes = self.store.recipes()
        feedback = self.store.feedback()
        need = self.config.cooldown_weeks + 1
        day_themes = {d.theme.casefold() for d in self.config.days}
        out = []
        for theme in self.config.themes:
            themed = [r for r in recipes if r.has_tag(theme) and feedback.get(r.uid) != "nein"]
            kid = [r for r in themed if r.has_tag(self.config.kid_tag) or feedback.get(r.uid) == "ok"]
            out.append(
                {
                    "thema": theme,
                    "rezepte": len(themed),
                    "kinder_ok": len(kid),
                    "bedarf": need,
                    "hat_tag": theme.casefold() in day_themes,
                }
            )
        return out

    def sync_paprika(self) -> str:
        """Pull recipes from Paprika's cloud. Returns a short German summary."""
        if self.paprika is None:
            raise PaprikaError("Paprika-Sync ist nicht eingerichtet (PAPRIKA_EMAIL/PAPRIKA_PASSWORD fehlen)")
        cache = {uid: CachedRecipe(h, data) for uid, (h, data) in self.store.paprika_cache().items()}
        result = self.paprika.sync(cache)
        self.store.replace_paprika_cache({uid: (c.hash, c.data) for uid, c in result.cache.items()})
        count = self.store.replace_recipes(result.recipes)
        if result.categories:
            self.store.set_meta("paprika_categories", json.dumps(result.categories, ensure_ascii=False))
        summary = f"{count} Rezepte, {result.fetched} geladen, {result.removed} entfernt"
        self.store.set_meta("last_sync", f"{datetime.now():%d.%m.%Y %H:%M} – {summary}")
        self.store.set_meta("last_sync_error", "")
        return summary

    def try_sync_paprika(self) -> str | None:
        """Sync if configured; on failure keep the existing recipes and return the error."""
        if self.paprika is None:
            return None
        try:
            self.sync_paprika()
            return None
        except PaprikaError as e:
            self.store.set_meta("last_sync_error", f"{datetime.now():%d.%m.%Y %H:%M} – {e}")
            return str(e)

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
                if m.day == day and m.recipe_uid is None
                else m
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
        body = json.dumps(
            {
                "titel": "Mahlzeit",
                "nachricht": f"Plan für die Woche ab {plan.week_start:%d.%m.} ist da",
                "woche": plan.week_start.isoformat(),
                "gerichte": [m.name for m in plan.meals],
            }
        ).encode()
        req = urllib.request.Request(self.webhook_url, body, {"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5).close()
        except OSError:
            pass  # notification is best effort
