"""Use cases shared by web app and CLI: glue between store, planner and config."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from . import ics, ideas, proposals, season, shopping, webrecipe
from .config import WEEKDAY_NAMES, PlanConfig
from .holidays import holidays_between
from .llm import LLMClient, LLMError
from .models import Meal, Recipe, Verdict, WeekPlan, with_cooked
from .paprika import recipe_from_json
from .paprika_sync import CachedRecipe, PaprikaClient, PaprikaError
from .planner import ANY_THEME, is_lunch, is_quick, minutes, plan_week, week_start_of
from .prep import prep_hints
from .store import Store
from .tagging import Change, apply_changes

OVERRIDE = "-"  # skipdays marker: cook on this holiday after all
log = logging.getLogger(__name__)


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Tischlein/0.1"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        if not resp.headers.get("Content-Type", "").startswith("image/"):
            raise OSError("keine Bilddatei")
        return resp.read()


WEB_AGENT = "Mozilla/5.0 (compatible; Tischlein)"
WEB_IMAGES = ("image/jpeg", "image/png", "image/webp")


class _PublicOnly(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to another public http(s) address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_public(url: str) -> None:
    host = urllib.parse.urlsplit(webrecipe.check_url(url)).hostname
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError as e:
        raise ValueError(f"Die Adresse {host} ist nicht erreichbar.") from e
    if not addresses or not all(webrecipe.is_public(a.split("%")[0]) for a in addresses):
        raise ValueError("Links ins eigene Netz holt Tischlein nicht.")


def _web_get(url: str, max_bytes: int, types: tuple[str, ...] = ()) -> tuple[bytes, str]:
    """GET a public web page or image: (body, content type). At most `max_bytes`, 10 s."""
    _check_public(url)
    opener = urllib.request.build_opener(_PublicOnly)
    headers = {"User-Agent": WEB_AGENT, "Accept-Language": "de-DE,de;q=0.9"}
    if types:  # some sites answer one address with a page or with data, depending on what is asked for
        headers["Accept"] = ", ".join(types)
    req = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(req, timeout=10) as resp:
            kind = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if types and kind not in types:
                raise ValueError(f"Unerwarteter Inhalt ({kind or 'unbekannt'}).")
            body = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        raise ValueError(f"Die Seite antwortet mit Fehler {e.code}.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ValueError("Die Seite ist gerade nicht erreichbar.") from e
    if len(body) > max_bytes:
        raise ValueError("Die Seite ist zu groß.")
    return body, kind


def _removed_by_hand(removed: dict[str, str], item: dict) -> bool:
    """Deleted from this week's list and needed by no dish other than back then ({item_key: recipes})."""
    was_for = removed.get(shopping.item_key(item["name"]))
    if was_for is None:
        return False
    return _dish_set(item.get("recipes", "")) <= _dish_set(was_for)


def _dish_set(recipes: str) -> set[str]:
    return {d.strip() for d in recipes.split(",") if d.strip()}


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
    shopping_delay: float | None = 3.0  # seconds to wait for more plan changes; None = collect right away

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
        self._sync_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._sync_state = {"laeuft": False, "phase": "", "fertig": 0, "gesamt": 0, "ergebnis": None, "fehler": None}
        self._sync_thread: threading.Thread | None = None
        self._ideas_state = {"laeuft": False, "fertig": 0, "gesamt": 0, "ergebnis": None, "fehler": None}
        self._ideas_thread: threading.Thread | None = None
        self._proposal_state = {"laeuft": False, "fertig": 0, "gesamt": 0, "ergebnis": None, "fehler": None}
        self._proposal_thread: threading.Thread | None = None
        self._kid_thread: threading.Thread | None = None
        self._counters_after_sync: str | None = None
        self._auto_thread: threading.Thread | None = None
        self._auto_stop = threading.Event()
        self._shopping_pending: dict[date, threading.Timer | None] = {}
        self._llm_transport = None  # tests inject a fake transport
        self.web_get = _web_get  # tests inject fake pages
        self._cookidoo_key: dict | None = None  # for Cookidoo's search index, see _search_cookidoo
        self._web_cache: dict[str, dict] = {}
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
        except ValueError, TypeError:
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
        return ics.render([self.eaten(p) for p in self.store.approved_plans()], cfg, name=s["name"])

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
        kid, new = self.config.kid_tag.casefold(), self.config.new_tag
        if any(x.casefold() == kid for x in remove) and not recipe.has_tag(new):
            if self.store.feedback().get(uid) == "ok":  # Kinder-OK learned from a rating goes with the category
                self.store.remove_feedback(uid)
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
        if not key:  # a Web-Rezept: its photo lives only here
            web = self._web_photo_path(uid)
            return web if web and web.exists() else None
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

    def sync_paprika(self, progress=None) -> str:
        """Pull recipes from Paprika's cloud. Returns a short German summary. One sync at a time."""
        if self.paprika is None:
            raise PaprikaError("Paprika-Sync ist nicht eingerichtet (PAPRIKA_EMAIL/PAPRIKA_PASSWORD fehlen)")
        with self._sync_lock:
            return self._sync(progress)

    def _sync(self, progress) -> str:
        cache = {uid: CachedRecipe(h, data) for uid, (h, data) in self.store.paprika_cache().items()}
        result = self.paprika.sync(cache, progress=progress)
        self.store.replace_paprika_cache({uid: (c.hash, c.data) for uid, c in result.cache.items()})
        count = self.store.replace_recipes(result.recipes)
        if result.categories:
            self.store.set_meta("paprika_categories", json.dumps(result.categories, ensure_ascii=False))
        summary = f"{count} Rezepte, {result.fetched} geladen, {result.removed} entfernt"
        self.store.set_meta("last_sync", f"{datetime.now():%d.%m.%Y %H:%M} – {summary}")
        self.store.set_meta("last_sync_error", "")
        return summary

    def sync_status(self) -> dict:
        with self._state_lock:
            return dict(self._sync_state)

    def start_sync(self) -> dict:
        """Run the sync in a background thread (the first full sync takes minutes) and return its state."""
        if self.paprika is None:
            raise PaprikaError("Paprika-Sync ist nicht eingerichtet (PAPRIKA_EMAIL/PAPRIKA_PASSWORD fehlen)")
        with self._state_lock:
            if self._sync_state["laeuft"]:
                return dict(self._sync_state)
            self._sync_state = {
                "laeuft": True,
                "phase": "start",
                "fertig": 0,
                "gesamt": 0,
                "ergebnis": None,
                "fehler": None,
            }
            state = dict(self._sync_state)
        self._sync_thread = threading.Thread(target=self._run_background_sync, name="paprika-sync", daemon=True)
        self._sync_thread.start()
        return state

    def _run_background_sync(self) -> None:
        def progress(phase: str, done: int, total: int) -> None:
            with self._state_lock:
                self._sync_state.update(phase=phase, fertig=done, gesamt=total)

        result: dict = {}
        try:
            result["ergebnis"] = self.sync_paprika(progress=progress)
            if self._counters_after_sync:  # remember what this sync covered, so the next check can skip
                self.store.set_meta("paprika_counters", self._counters_after_sync)
        except PaprikaError as e:
            self.store.set_meta("last_sync_error", f"{datetime.now():%d.%m.%Y %H:%M} – {e}")
            result["fehler"] = str(e)
        except Exception as e:  # noqa: BLE001 - surface anything in the UI instead of a silent dead thread
            result["fehler"] = f"Unerwarteter Fehler: {e}"
            raise
        finally:
            self._counters_after_sync = None
            with self._state_lock:
                self._sync_state.update(laeuft=False, phase="fertig", **result)

    # LLM -----------------------------------------------------------------------

    def llm_settings(self) -> dict:
        return {
            "base_url": self.store.meta("llm_base_url") or "",
            "model": self.store.meta("llm_model") or "",
            "hat_schluessel": bool(self.store.meta("llm_api_key")),
            "aktiv": self.llm() is not None,
        }

    def llm(self) -> LLMClient | None:
        base, key, model = (self.store.meta(k) for k in ("llm_base_url", "llm_api_key", "llm_model"))
        if not (base and key and model):
            return None
        return LLMClient(base, key, model, **({"transport": self._llm_transport} if self._llm_transport else {}))

    def save_llm(self, base_url: str, api_key: str, model: str) -> None:
        """Test the connection with a tiny request, then store. An empty key keeps the stored one."""
        base_url, model = base_url.strip(), model.strip()
        api_key = api_key.strip() or (self.store.meta("llm_api_key") or "")
        if not (base_url and api_key and model):
            raise ValueError("Bitte Basis-URL, API-Schlüssel und Modell angeben.")
        client = LLMClient(
            base_url, api_key, model, **({"transport": self._llm_transport} if self._llm_transport else {})
        )
        answer = client.chat_json("Antworte nur mit JSON.", 'Gib {"ok": true} zurück.', temperature=0)
        if not isinstance(answer, dict):
            raise LLMError("Das Modell hat kein JSON-Objekt geliefert.")
        self.store.set_meta("llm_base_url", base_url)
        self.store.set_meta("llm_api_key", api_key)
        self.store.set_meta("llm_model", model)

    def clear_llm(self) -> None:
        for k in ("llm_base_url", "llm_api_key", "llm_model"):
            self.store.set_meta(k, "")

    # People (from Cloudflare Access) ---------------------------------------------

    def people(self) -> dict[str, str]:
        return json.loads(self.store.meta("personen") or "{}")

    def person_name(self, email: str | None) -> str:
        if not email:
            return ""
        people = self.people()
        if email not in people:
            people[email] = ""
            self.store.set_meta("personen", json.dumps(people, ensure_ascii=False))
        return people.get(email) or email

    def save_people(self, names: dict[str, str]) -> None:
        people = self.people()
        people.update({e: n.strip() for e, n in names.items() if e in people})
        self.store.set_meta("personen", json.dumps(people, ensure_ascii=False))

    # Shopping list -------------------------------------------------------------

    def _planned(self, week_start: date) -> list[tuple]:
        """(meal, raw recipe, its own portions, planned portions, changed?) per cooked day of the week."""
        plan = self.store.plan(week_start)
        chosen = self.store.servings(week_start, week_start + timedelta(days=6))
        cooked = self.store.cooked(week_start, week_start + timedelta(days=6))  # already eaten: nothing to buy
        out = []
        for m in plan.meals if plan else []:
            if not m.recipe_uid or m.skipped or m.leftovers or m.day in cooked:
                continue
            entry = self.store.raw_recipe(m.recipe_uid)
            raw = entry[1] if entry else {}
            base = shopping.base_servings(raw.get("servings")) or self.config.portions  # unknown: as written
            n = chosen.get((m.day, m.recipe_uid), self.config.portions)
            out.append((m, raw, base, n, (m.day, m.recipe_uid) in chosen))
        return out

    def week_servings(self, week_start: date) -> dict[date, dict]:
        """Per cooked day: the recipe's own portions, the planned ones (the family's by default)
        and whether they were changed for that day."""
        return {m.day: {"base": b, "servings": n, "custom": c} for m, _, b, n, c in self._planned(week_start)}

    def set_servings(self, day: date, servings: int) -> int:
        """Portions for the dish cooked on `day` (1-30). Returns what is now planned."""
        plan = self.store.plan(week_start_of(day))
        meal = next((m for m in plan.meals if m.day == day and m.recipe_uid and not m.skipped), None) if plan else None
        if meal is None or meal.leftovers:
            raise KeyError(f"Am {day:%d.%m.} ist kein Gericht geplant.")
        servings = max(1, min(30, servings))
        with self._watch(week_start_of(day)):
            self.store.set_servings(day, meal.recipe_uid, None if servings == self.config.portions else servings)
        return servings

    def _week_recipes(self, week_start: date) -> list[dict]:
        return [
            {"name": m.name, "ingredients": raw.get("ingredients", ""), "factor": n / base}
            for m, raw, base, n, _ in self._planned(week_start)
            if raw
        ]

    def generate_shopping(self, week_start: date) -> str:
        """Build the week's list. Returns how: "llm", "einfach" or "einfach (LLM-Fehler: ...)"."""
        recipes = self._week_recipes(week_start)
        if not recipes:
            raise KeyError("Für diese Woche gibt es keinen Plan mit Gerichten.")
        items = shopping.collect(recipes, self.config.pantry)
        how = "einfach"
        result = []
        client = self.llm()
        if client is not None:
            try:
                result = shopping.parse_llm(client.chat_json(shopping.SYSTEM, shopping.llm_prompt(items)))
                how = "llm"
            except LLMError as e:
                how = f"einfach (LLM-Fehler: {e})"
        if not result:
            result = shopping.merge(items)
        removed = self.store.removed_shopping(week_start)
        result = [r for r in result if not _removed_by_hand(removed, r)]
        before = self.store.shopping(week_start)
        old = {shopping.item_key(i["name"]): i for i in before if not i["manual"]}
        for r in result:  # an ingredient still on the list keeps its tick and its Bring state
            prev = old.get(shopping.item_key(r["name"]))
            if prev:
                r.update(
                    done=prev["done"], done_by=prev["done_by"], bring=prev["bring"], bring_menge=prev["bring_menge"]
                )
        self.store.replace_generated_shopping(week_start, result)
        kept = {shopping.item_key(r["name"]) for r in result}
        gone = [
            i["name"] for i in before if i["bring"] and not i["manual"] and shopping.item_key(i["name"]) not in kept
        ]
        self.push_bring(week_start, gone)  # dishes swapped out: their ingredients leave Bring too
        return how

    def has_shopping(self, week_start: date) -> bool:
        """Was the week's list collected (even if every collected ingredient was deleted since)?"""
        items = self.store.shopping(week_start)
        return any(not i["manual"] for i in items) or bool(self.store.removed_shopping(week_start))

    def shopping_updating(self, week_start: date) -> bool:
        return week_start in self._shopping_pending

    def _dish_key(self, week_start: date) -> list:
        """What the list depends on: each dish with its portions, not the day it is cooked."""
        return sorted((m.recipe_uid, round(n / base, 4)) for m, _, base, n, _ in self._planned(week_start))

    @contextmanager
    def _watch(self, week_start: date):
        """Collect the list again when the dishes of a collected week change."""
        before = self._dish_key(week_start)
        yield
        if self._dish_key(week_start) != before and self.has_shopping(week_start):
            self.refresh_shopping(week_start)

    def refresh_shopping(self, week_start: date) -> None:
        """Collect the list in the background, `shopping_delay` seconds after the last change, so a few
        clicks in a row call the LLM once. Ticks, Bring state and own entries stay (generate_shopping)."""

        def run() -> None:
            try:
                self.generate_shopping(week_start)
            except KeyError:
                pass  # no dish left this week
            except Exception:
                log.exception("Einkaufsliste für %s nicht neu gesammelt", week_start)
            finally:
                with self._state_lock:
                    if self._shopping_pending.get(week_start) is timer:
                        del self._shopping_pending[week_start]

        timer = None
        if self.shopping_delay is None:
            self._shopping_pending[week_start] = None
            run()
            return
        with self._state_lock:
            old = self._shopping_pending.get(week_start)
            if old is not None:
                old.cancel()
            timer = threading.Timer(self.shopping_delay, run)
            timer.daemon = True
            self._shopping_pending[week_start] = timer
        timer.start()

    # Ideas ---------------------------------------------------------------------

    def generate_ideas(self, count: int = 6, progress=None, theme: str | None = None) -> int:
        """Ask the LLM for `count` ideas, one dish per request; `theme` asks for that theme only.

        Single-dish answers are short (a long list tends to come back cut off and is then no valid
        JSON), each idea is stored as soon as it arrives, and `progress(done, total)` can report
        real progress. A failed request is skipped; only when none succeeds is the error raised.
        """
        client = self.llm()
        if client is None:
            raise LLMError("Kein LLM eingerichtet. In den Einstellungen Basis-URL, Schlüssel und Modell eintragen.")
        recipes = self.store.recipes()
        feedback = self.store.feedback()
        by_uid = {r.uid: r for r in recipes}
        verdicts: dict[str, str] = {}
        for rt in self.store.ratings():
            verdicts[rt["uid"]] = rt["verdict"]
        liked = [r.name for r in recipes if r.has_tag(self.config.kid_tag) or verdicts.get(r.uid) == "gut"]
        disliked = [by_uid[u].name for u, v in verdicts.items() if v == "schlecht" and u in by_uid]
        disliked += [by_uid[u].name for u, v in feedback.items() if v == "nein" and u in by_uid]
        themes = list(self.config.themes)
        supply = self.theme_supply()
        if theme is not None and theme not in themes:
            raise LLMError(f"Unbekanntes Thema: {theme}")
        focus = [theme] if theme else ideas.focus_themes(supply, themes)
        open_ideas = [i["name"] for i in self.store.ideas("neu")]
        suggested: list[str] = []
        error: LLMError | None = None
        if progress:
            progress(0, count)
        for n in range(count):
            prompt = ideas.build_prompt(
                liked,
                sorted(set(disliked)),
                [r.name for r in recipes],
                supply,
                themes,
                1,
                date.today(),
                theme=focus[n % len(focus)] if focus else None,
                suggested=open_ideas + suggested,
                avoid=self.dislikes(),
            )
            try:
                found = ideas.parse_ideas(client.chat_json(ideas.SYSTEM, prompt, temperature=0.8), themes)[:1]
            except LLMError as e:
                error = e
                found = []
            self.store.add_ideas(found)
            suggested += [i["name"] for i in found]
            if progress:
                progress(n + 1, count)
        if not suggested and error is not None:
            raise error
        return len(suggested)

    def ideas_status(self) -> dict:
        with self._state_lock:
            return dict(self._ideas_state)

    def start_ideas(self, count: int = 6, theme: str | None = None) -> dict:
        """Generate ideas in a background thread (several LLM calls take a minute) and return the state."""
        if self.llm() is None:
            raise LLMError("Kein LLM eingerichtet. In den Einstellungen Basis-URL, Schlüssel und Modell eintragen.")
        if theme is not None and theme not in self.config.themes:
            raise LLMError(f"Unbekanntes Thema: {theme}")
        with self._state_lock:
            if self._ideas_state["laeuft"]:
                return dict(self._ideas_state)
            self._ideas_state = {"laeuft": True, "fertig": 0, "gesamt": count, "ergebnis": None, "fehler": None}
            state = dict(self._ideas_state)
        self._ideas_thread = threading.Thread(
            target=self._run_background_ideas, args=(count, theme), name="llm-ideas", daemon=True
        )
        self._ideas_thread.start()
        return state

    def _run_background_ideas(self, count: int, theme: str | None = None) -> None:
        def progress(done: int, total: int) -> None:
            with self._state_lock:
                self._ideas_state.update(fertig=done, gesamt=total)

        result: dict = {}
        try:
            result["ergebnis"] = self.generate_ideas(count, progress=progress, theme=theme)
        except LLMError as e:
            result["fehler"] = str(e)
        except Exception as e:  # noqa: BLE001 - surface anything in the UI instead of a silent dead thread
            result["fehler"] = f"Unerwarteter Fehler: {e}"
            raise
        finally:
            with self._state_lock:
                self._ideas_state.update(laeuft=False, **result)

    def _ensure_categories(self, names: list[str]) -> list[str]:
        cats = [c for c in self.paprika.categories_raw() if not c.get("deleted")]
        by_name = {c["name"].casefold(): c["uid"] for c in cats}
        order = max((c.get("order_flag") or 0 for c in cats), default=0)
        uids = []
        for name in names:
            if name.casefold() not in by_name:
                order += 1
                by_name[name.casefold()] = self.paprika.create_category(name, order)
            uids.append(by_name[name.casefold()])
        return uids

    def accept_idea(self, idea_id: int) -> Recipe:
        """Create the idea as a new recipe in Paprika (categories: theme, main dish, new, AI)."""
        if self.paprika is None:
            raise PaprikaError("Paprika-Sync ist nicht eingerichtet.")
        idea = self.store.idea(idea_id)
        if idea is None or idea["status"] != "neu":
            raise KeyError("Diesen Vorschlag gibt es nicht mehr.")
        names = [n for n in (idea.get("thema"), self.config.main_tag, self.config.new_tag, self.config.ai_tag) if n]
        uid = str(uuid.uuid4()).upper()
        with self._paprika_lock:
            data = ideas.paprika_recipe(
                idea, uid, self._ensure_categories(names), f"{datetime.now():%Y-%m-%d %H:%M:%S}"
            )
            self.paprika.update_recipe(data)
            self.paprika.notify()
            raw = self.paprika.recipe(uid)
            cat_names = self.paprika.categories()
        recipe = recipe_from_json(raw, cat_names)
        self.store.upsert_recipe(recipe, raw.get("hash", ""), raw)
        self.store.set_idea_status(idea_id, "uebernommen", uid)
        return recipe

    # Web-Rezepte -----------------------------------------------------------------

    def web_categories(self) -> list[str]:
        """Categories offered in the preview: family tags first, then the themes."""
        cfg = self.config
        return [cfg.new_tag, cfg.main_tag, cfg.kid_tag, *cfg.themes]

    def find_source(self, url: str) -> Recipe | None:
        """A recipe in the collection that came from this page (same link without tracking parts)."""
        key = webrecipe.normalize_url(url)
        same = (r for r in self.store.recipes() if r.source_url and webrecipe.normalize_url(r.source_url) == key)
        return next(same, None)

    def web_recipe(self, url: str) -> dict:
        """Read a recipe from a page for the preview: the page's schema.org data, else via the LLM.
        Raises ValueError with a reason the family can read."""
        url = webrecipe.check_url(webrecipe.find_url(url) or url)
        key = webrecipe.normalize_url(url)
        if key in self._web_cache:
            return self._web_cache[key]
        body, kind = self.web_get(url, 3_000_000, ("text/html", "application/xhtml+xml"))
        html = body.decode("utf-8", errors="replace")
        recipe = webrecipe.from_schema(html, url)
        if recipe is None:
            client = self.llm()
            if client is None:
                raise ValueError("Auf dieser Seite steht kein Rezept, das Tischlein lesen kann.")
            try:
                answer = client.chat_json(webrecipe.SYSTEM, webrecipe.llm_prompt(webrecipe.page_text(html)), 0.1)
            except LLMError as e:
                raise ValueError(f"Das LLM konnte die Seite nicht lesen: {e}") from e
            recipe = webrecipe.from_llm(answer, html, url)
            if recipe is None:
                raise ValueError("Auf dieser Seite steht kein Rezept, das Tischlein lesen kann.")
        if not recipe["steps"] and webrecipe.is_cookidoo_recipe(url):
            try:  # the page leaves the steps out; without them the recipe still has its ingredients
                body, _ = self.web_get(webrecipe.clean_url(url), 1_000_000, ("application/json",))
                recipe.update(webrecipe.cookidoo_details(json.loads(body)))
            except ValueError:
                pass
        recipe["theme"] = webrecipe.suggest_theme(recipe, list(self.config.themes))
        if len(self._web_cache) > 20:
            self._web_cache.pop(next(iter(self._web_cache)))
        self._web_cache[key] = recipe
        return recipe

    def accept_web_recipe(self, url: str, categories: list[str]) -> Recipe:
        """Übernehmen: create the Web-Rezept in Paprika with its source, the chosen categories and its
        photo (JPEG). A photo Paprika does not take (other format, upload refused) is kept locally."""
        if self.paprika is None:
            raise PaprikaError("Paprika-Sync ist nicht eingerichtet.")
        existing = self.find_source(url)
        if existing:
            raise ValueError(f"Schon in Paprika: {existing.name}")
        recipe = self.web_recipe(url)
        offered = {c.casefold(): c for c in self.web_categories()}
        names = list(dict.fromkeys(offered[c.casefold()] for c in categories if c.casefold() in offered))
        uid = str(uuid.uuid4()).upper()
        photo, kind = b"", ""
        if recipe.get("image"):
            try:
                photo, kind = self.web_get(recipe["image"], 5_000_000, WEB_IMAGES)
            except ValueError:
                photo = b""  # no photo is fine
        with self._paprika_lock:
            data = webrecipe.paprika_recipe(
                recipe, uid, self._ensure_categories(names), f"{datetime.now():%Y-%m-%d %H:%M:%S}"
            )
            uploaded = False
            if photo and kind == "image/jpeg":
                try:
                    self.paprika.update_recipe(data, photo=photo)
                    uploaded = True
                except PaprikaError:
                    log.warning("Paprika hat das Foto zu %s nicht angenommen", recipe["name"])
            if not uploaded:
                self.paprika.update_recipe(data)
            self.paprika.notify()
            raw = self.paprika.recipe(uid)
            cat_names = self.paprika.categories()
        created = recipe_from_json(raw, cat_names)
        self.store.upsert_recipe(created, raw.get("hash", ""), raw)
        if photo and not uploaded:
            self._save_web_photo(uid, photo)
        self._web_cache.pop(webrecipe.normalize_url(url), None)
        return created

    def _web_photo_path(self, uid: str) -> Path | None:
        return self.backup_dir / "bilder" / f"{uid}-web.jpg" if self.backup_dir else None

    def _save_web_photo(self, uid: str, photo: bytes) -> None:
        path = self._web_photo_path(uid)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(photo)

    def web_photo_uids(self) -> set[str]:
        folder = self.backup_dir / "bilder" if self.backup_dir else None
        if folder is None or not folder.exists():
            return set()
        return {p.name.removesuffix("-web.jpg") for p in folder.glob("*-web.jpg")}

    def dismiss_idea(self, idea_id: int) -> None:
        self.store.set_idea_status(idea_id, "verworfen")

    # Preparation and reminder --------------------------------------------------------

    def day_info(self, day: date, approved_only: bool = False) -> dict:
        """What is for lunch on `day`, with preparation hints (for Home Assistant)."""
        plan = self.eaten_plan(week_start_of(day))
        meal = next((m for m in plan.meals if m.day == day), None) if plan else None
        info = {"datum": day.isoformat(), "gericht": "nichts geplant", "frei": False, "vorbereitung": []}
        skip = self.skip_map(week_start_of(day))
        if day in skip and not (meal and meal.recipe_uid):
            return {**info, "gericht": "Kein Mittagessen", "frei": True, "grund": skip[day]}
        if meal is None or (approved_only and plan.status != "freigegeben"):
            return info
        if meal.skipped:
            return {**info, "gericht": "Kein Mittagessen", "frei": True, "grund": meal.reason}
        entry = self.store.raw_recipe(meal.recipe_uid) if meal.recipe_uid else None
        raw = entry[1] if entry else {}
        return {
            **info,
            "gericht": meal.name,
            "thema": meal.theme,
            "neu": meal.is_new,
            "freigegeben": plan.status == "freigegeben",
            "zeit": (raw.get("total_time") or "").strip(),
            "vorbereitung": prep_hints(raw.get("ingredients", ""), raw.get("directions", "")),
        }

    def reminder_tick(self, now: datetime | None = None) -> bool:
        """Send tomorrow's dish (and its preparation) to Home Assistant once a day after reminder_time."""
        now = now or datetime.now()
        if not (self.webhook_url and self.config.reminder_time):
            return False
        if f"{now:%H:%M}" < self.config.reminder_time or self.store.meta("erinnert_am") == now.date().isoformat():
            return False
        info = self.day_info(now.date() + timedelta(days=1), approved_only=True)
        self.store.set_meta("erinnert_am", now.date().isoformat())
        if info["frei"] or info["gericht"] == "nichts geplant":
            return False
        text = f"Morgen gibt's {info['gericht']}."
        if info["vorbereitung"]:
            text += " Vorbereitung: " + "; ".join(info["vorbereitung"]) + "."
        self._post_webhook({"titel": "Tischlein", "nachricht": text, "morgen": info})
        return True

    def _settle(self, key: str, value: str, sent: bool = True) -> bool:
        """Remember that the message `key` is done for `value` (a day or a week). Not when Home
        Assistant did not take it (`sent` False): then the next tick, a minute later, tries again."""
        if sent:
            self.store.set_meta(key, value)
        return sent

    def rating_tick(self, now: datetime | None = None) -> bool:
        """„Wie war's?“ once after lunch, when today's dish has no rating yet. Home Assistant shows
        the answers as buttons and sends the chosen one to /api/bewertung."""
        now = now or datetime.now()
        if not (self.webhook_url and self.config.rating_time) or f"{now:%H:%M}" < self.config.rating_time:
            return False
        today = now.date()
        key = "bewertung_gefragt"
        if self.store.meta(key) == today.isoformat():
            return False
        plan = self.eaten_plan(week_start_of(today))
        meal = next((m for m in plan.meals if m.day == today), None) if plan else None
        nothing = meal is None or not meal.recipe_uid or meal.skipped or meal.leftovers
        if nothing or (plan.status != "freigegeben" and not meal.logged):
            return False  # a Nachtrag later today may still be asked about
        if any(r["uid"] == meal.recipe_uid and r["day"] == today for r in self.store.ratings()):
            return not self._settle(key, today.isoformat())
        payload = {
            "titel": "Tischlein",
            "nachricht": f"Wie war {meal.name}?",
            "art": "bewertung",
            "datum": today.isoformat(),
            "gericht": meal.name,
        }
        return self._settle(key, today.isoformat(), self._post_webhook(payload))

    @staticmethod
    def _due(now: datetime, when: tuple[int, str] | None) -> bool:
        """Is it, within this week, at or after weekday and time `when`?"""
        return when is not None and (now.weekday(), f"{now:%H:%M}") >= when

    def _draft_message(self, plan: WeekPlan, lead: str) -> dict:
        days = ", ".join(
            f"{WEEKDAY_NAMES[m.day.weekday()][:2]} {m.name}" for m in plan.meals if m.recipe_uid and not m.skipped
        )
        return {
            "titel": "Tischlein",
            "nachricht": f"{lead} {days}." if days else lead,
            "art": "entwurf",
            "woche": plan.week_start.isoformat(),
        }

    def draft_tick(self, now: datetime | None = None) -> bool:
        """Once a week: make sure next week has a draft and tell the family (never approves by itself)."""
        now = now or datetime.now()
        if not self._due(now, self.config.draft_time):
            return False
        week_start = week_start_of(now.date()) + timedelta(weeks=1)
        key, week = "entwurf_fuer", week_start.isoformat()
        if self.store.meta(key) == week:
            return False
        plan = self.store.plan(week_start)
        if plan is None:
            self.try_sync_paprika()
            if not self.store.recipes():
                return False  # nothing to plan with yet; the next tick looks again
            plan = self._generate(week_start, today=now.date())
        if plan.status == "freigegeben" or not self.webhook_url:
            return not self._settle(key, week)
        lead = f"Entwurf für die Woche ab {week_start:%d.%m.}:"
        return self._settle(key, week, self._post_webhook(self._draft_message(plan, lead)))

    def approve_tick(self, now: datetime | None = None) -> bool:
        """Once, late in the week: remind when next week's draft still waits for approval."""
        now = now or datetime.now()
        if not (self.webhook_url and self._due(now, self.config.approve_time)):
            return False
        week_start = week_start_of(now.date()) + timedelta(weeks=1)
        key, week = "freigabe_erinnert", week_start.isoformat()
        if self.store.meta(key) == week:
            return False
        plan = self.store.plan(week_start)
        if plan is None or plan.status == "freigegeben":
            return not self._settle(key, week)
        lead = f"Die Woche ab {week_start:%d.%m.} ist noch nicht freigegeben:"
        return self._settle(key, week, self._post_webhook(self._draft_message(plan, lead)))

    def bring_lists(self) -> list[dict]:
        """Bring lists as Home Assistant last reported them: [{entity_id, name}]."""
        try:
            lists = json.loads(self.store.meta("bring_lists") or "[]")
        except ValueError:
            return []
        return [x for x in lists if isinstance(x, dict) and str(x.get("entity_id", "")).startswith("todo.")]

    def save_bring_lists(self, lists: list[dict]) -> int:
        clean = [
            {"entity_id": str(x["entity_id"]), "name": str(x.get("name") or x["entity_id"])}
            for x in lists
            if isinstance(x, dict) and str(x.get("entity_id", "")).startswith("todo.")
        ]
        self.store.set_meta("bring_lists", json.dumps(clean, ensure_ascii=False))
        return len(clean)

    def bring_list(self) -> str:
        """The chosen Bring list (entity id); empty = Home Assistant's default."""
        chosen = self.store.meta("bring_list") or ""
        return chosen if any(x["entity_id"] == chosen for x in self.bring_lists()) else ""

    def set_bring_list(self, entity_id: str) -> None:
        if entity_id and not any(x["entity_id"] == entity_id for x in self.bring_lists()):
            raise KeyError("Diese Bring-Liste kennt Home Assistant nicht.")
        self.store.set_meta("bring_list", entity_id)

    # Bring sync ----------------------------------------------------------------
    #
    # Tischlein -> Bring: after every change of a week's list the whole wanted state goes to Home
    # Assistant (art: bring_sync): open items to add or update, bought ones to tick off. Idempotent,
    # so sending it twice does no harm. Bring -> Tischlein: Home Assistant reports the Bring list
    # (apply_bring_state): what was ticked off or removed there is ticked off here, what was added
    # there comes in as "Aus Bring".

    BRING_GROUP = "Aus Bring"

    def bring_sync_on(self) -> bool:
        """Off until switched on in the settings."""
        return bool(self.webhook_url) and self.store.meta("bring_sync") == "1"

    def set_bring_sync(self, on: bool) -> None:
        self.store.set_meta("bring_sync", "1" if on else "0")

    def _bring_changes(self, week_start: date, gone: list[str] = (), everything: bool = False):
        """What Bring still has to learn: open items new to it or with another amount, items ticked off
        here that are still open there, and names that left the list. `everything` resends all."""
        items = self.store.shopping(week_start)
        add = [i for i in items if not i["done"] and (everything or i["bring"] != 1 or i["bring_menge"] != i["amount"])]
        done = [i for i in items if i["done"] and (i["bring"] == 1 or (everything and i["bring"]))]
        payload = {
            "titel": "Einkaufsliste",
            "art": "bring_sync",
            "liste": self.bring_list(),
            "offen": [{"name": i["name"], "menge": i["amount"]} for i in add],
            "erledigt": list(dict.fromkeys([i["name"] for i in done] + list(gone))),
        }
        state = [(i["id"], 1, i["amount"]) for i in add] + [(i["id"], 2, i["bring_menge"]) for i in done]
        return payload, state, sum(1 for i in items if not i["done"])

    def push_bring(self, week_start: date, gone: list[str] = ()) -> None:
        """Send what changed on the week's list to Bring, in the background (when the sync is on)."""
        if not self.bring_sync_on():
            return
        payload, state, _ = self._bring_changes(week_start, gone)
        self.store.set_meta("bring_week", week_start.isoformat())
        if payload["offen"] or payload["erledigt"]:
            # remembered only once Home Assistant has it: an item Bring never got must not count as bought
            self._post_async(payload, lambda: self.store.set_bring_state(state))

    def send_to_bring(self, week_start: date, person: str = "") -> int:
        """ "Bring abgleichen" by hand: the whole list again, awaited so a failure is reported. Bring
        keeps one item per name, so this does not duplicate anything. Returns the open items."""
        if not self.webhook_url:
            raise LookupError("Home Assistant ist nicht verbunden (HA_WEBHOOK_URL fehlt).")
        payload, state, open_count = self._bring_changes(week_start, everything=True)
        if not self._post_webhook({**payload, "von": person}):
            raise LookupError("Home Assistant ist nicht erreichbar, Bring wurde nicht aktualisiert.")
        self.store.set_bring_state(state)
        self.store.set_meta("bring_week", week_start.isoformat())
        return open_count

    def apply_bring_state(self, liste: str, entries: list[dict], today: date | None = None) -> dict:
        """Take over the Bring list as Home Assistant reports it ({summary, description, status}).

        Only for the chosen list (Home Assistant resolves the default itself, see /api/bring/ziel) and
        the week last synced to Bring (else this week). An empty report ticks nothing off: that is a
        failed read far more often than a list that was bought empty.
        Returns what changed: {"abgehakt": n, "neu": n}."""
        chosen = self.bring_list()
        if chosen and liste != chosen:
            return {"abgehakt": 0, "neu": 0}
        synced = self.store.meta("bring_week")
        week_start = date.fromisoformat(synced) if synced else week_start_of(today or date.today())
        items = self.store.shopping(week_start)
        by_key: dict[str, dict] = {}
        for e in entries:
            name = str(e.get("summary") or "").strip()
            if name:
                by_key[shopping.item_key(name)] = {
                    "name": name,
                    "menge": str(e.get("description") or "").strip(),
                    "open": e.get("status") != "completed",
                }
        bought = []
        if entries:
            for i in items:
                if i["done"] or i["bring"] != 1:
                    continue
                there = by_key.get(shopping.item_key(i["name"]))
                if there is None or not there["open"]:  # bought (or removed) in Bring
                    bought.append(i["id"])
        self.store.bought_in_bring(bought)
        ticked = len(bought)
        # deleted here while Bring still had it open (the removal is still on its way): not back in
        known = {shopping.item_key(i["name"]) for i in items} | set(self.store.removed_shopping(week_start))
        new = 0
        for key, e in by_key.items():
            if e["open"] and key not in known:
                self.store.add_shopping_item(week_start, e["name"], e["menge"], self.BRING_GROUP, bring=True)
                new += 1
        return {"abgehakt": ticked, "neu": new}

    def set_item_done(self, item_id: int, done: bool, person: str = "") -> bool:
        week_start = self.store.shopping_week(item_id)
        if week_start is None or not self.store.set_shopping_done(item_id, done, person):
            return False
        self.push_bring(week_start)
        return True

    def _remember_removed(self, week_start: date, items: list[dict]) -> None:
        """Collected ingredients deleted here stay off the week's list (see _removed_by_hand)."""
        removed = {shopping.item_key(i["name"]): i["recipes"] for i in items if not i["manual"]}
        if removed:
            self.store.remember_removed_shopping(week_start, removed)

    def delete_item(self, item_id: int) -> list[dict]:
        """Delete one entry; returns it as it was, for Rückgängig (restore_items)."""
        week_start = self.store.shopping_week(item_id)
        if week_start is None:
            return []
        item = next((i for i in self.store.shopping(week_start) if i["id"] == item_id), None)
        if item is None:
            return []
        self._remember_removed(week_start, [item])
        self.store.delete_shopping_item(item_id)
        if item["bring"] == 1:
            self.push_bring(week_start, [item["name"]])
        return [item]

    def restore_items(self, week_start: date, items: list[dict]) -> int:
        """Rückgängig for deleting: the entries come back with their tick, and collecting again
        no longer leaves them out. Open ones go back to Bring when the sync is on."""
        rows = []
        for i in items:  # sent back by the browser: take only what a deleted entry can be
            try:
                rows.append(
                    {
                        "id": int(i["id"]),
                        "name": str(i["name"]).strip()[:200],
                        "amount": str(i.get("amount") or "")[:100],
                        "group": str(i.get("group") or "Sonstiges")[:100],
                        "recipes": str(i.get("recipes") or "")[:500],
                        "manual": bool(i.get("manual")),
                        "position": int(i.get("position") or 0),
                        "done": bool(i.get("done")),
                        "done_by": str(i.get("done_by") or "")[:100],
                    }
                )
            except KeyError, TypeError, ValueError, AttributeError:
                raise ValueError("Ungültiger Eintrag zum Wiederherstellen.") from None
        rows = [r for r in rows if r["name"]]
        back = self.store.restore_shopping(week_start, rows)
        if back:
            self.store.forget_removed_shopping(week_start, [shopping.item_key(r["name"]) for r in back])
            self.push_bring(week_start)
        return len(back)

    def set_all_done(self, week_start: date, done: bool, person: str = "") -> int:
        n = self.store.set_all_shopping_done(week_start, done, person)
        self.push_bring(week_start)
        return n

    def remove_done(self, week_start: date) -> list[dict]:
        """Delete every ticked entry; returns them as they were, for Rückgängig."""
        done = [i for i in self.store.shopping(week_start) if i["done"]]
        gone = [i["name"] for i in done if i["bring"] == 1]
        self._remember_removed(week_start, done)  # bought: collecting again must not bring it back open
        # exactly the ones read: one ticked on another phone meanwhile stays (and can be removed next time)
        self.store.delete_shopping_items([i["id"] for i in done])
        self.push_bring(week_start, gone)
        return done

    def add_shopping_item(self, week_start: date, name: str, amount: str = "", person: str = "") -> int:
        """Put something on the list by hand and tell Home Assistant (push to the phones)."""
        item_id = self.store.add_shopping_item(week_start, name, amount)
        self.push_bring(week_start)
        if self.webhook_url:
            what = f"{name} ({amount})" if amount else name
            who = person or "Jemand"
            payload = {
                "titel": "Einkaufsliste",
                "nachricht": f"{who} hat {what} auf die Liste gesetzt.",
                "art": "einkauf",
                "von": person,
                "eintrag": name,
                "menge": amount,
            }
            self._post_async(payload)
        return item_id

    def _post_async(self, payload: dict, on_success=None) -> None:
        """Send a notification in the background: the page must not wait for Home Assistant.
        `on_success` runs only when Home Assistant took it."""

        def run() -> None:
            if self._post_webhook(payload) and on_success:
                on_success()

        threading.Thread(target=run, name="ha-webhook", daemon=True).start()

    def _post_webhook(self, payload: dict) -> bool:
        req = urllib.request.Request(
            self.webhook_url, json.dumps(payload).encode(), {"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(req, timeout=5).close()
            return True
        except OSError:
            return False  # best effort; callers that must know check the result

    # Automatic sync -----------------------------------------------------------

    def auto_sync_tick(self) -> bool:
        """Ask Paprika's change counters and start a background sync if recipes or categories changed."""
        if self.paprika is None or self.sync_status()["laeuft"]:
            return False
        try:
            counters = self.paprika.change_counters()
        except PaprikaError as e:
            self.store.set_meta("auto_check", f"{datetime.now():%d.%m.%Y %H:%M} – {e}")
            return False
        key = json.dumps({k: counters.get(k) for k in ("recipes", "categories")}, sort_keys=True)
        self.store.set_meta("auto_check", f"{datetime.now():%d.%m.%Y %H:%M}")
        if key == self.store.meta("paprika_counters") and self.store.meta("last_sync"):
            return False
        self._counters_after_sync = key
        self.start_sync()
        return True

    def start_auto_sync(self) -> None:
        """Background loop: Paprika check every config.sync_minutes (0 = off), reminder once a day."""
        minutes = self.config.sync_minutes
        if self._auto_thread is not None:
            return

        def loop() -> None:
            if self._auto_stop.wait(20):  # let the server come up first
                return
            next_sync = 0.0
            while True:
                try:
                    if minutes > 0 and time.monotonic() >= next_sync:
                        next_sync = time.monotonic() + minutes * 60
                        self.auto_sync_tick()
                    for tick in (self.reminder_tick, self.rating_tick, self.draft_tick, self.approve_tick):
                        try:
                            tick()
                        except Exception:  # noqa: BLE001 - one failing message must not stop the others
                            logging.getLogger(__name__).exception("Meldung fehlgeschlagen")
                except Exception:  # noqa: BLE001 - never let the scheduler die
                    pass
                if self._auto_stop.wait(60):
                    return

        self._auto_thread = threading.Thread(target=loop, name="paprika-auto-sync", daemon=True)
        self._auto_thread.start()

    def stop_auto_sync(self) -> None:
        self._auto_stop.set()

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

    # Days without lunch ------------------------------------------------------

    def skip_map(self, week_start: date) -> dict[date, str]:
        """Days of a week without lunch: holidays (unless overridden) plus days marked free."""
        end = week_start + timedelta(days=6)
        days = {d: f"Feiertag: {n}" for d, n in holidays_between(week_start, end, self.config.holiday_state).items()}
        for d, reason in self.store.skipdays(week_start, end).items():
            if reason == OVERRIDE:
                days.pop(d, None)
            else:
                days[d] = reason
        return days

    def _set_day_free(self, day: date, reason: str) -> WeekPlan | None:
        self.store.set_skipday(day, reason.strip() or "Kein Mittagessen")
        return self._replan_day(day)

    def _set_day_cooking(self, day: date) -> WeekPlan | None:
        self._mark_cooking(day)
        return self._replan_day(day)

    def _mark_cooking(self, day: date) -> None:
        week_start = week_start_of(day)
        if day in holidays_between(week_start, week_start + timedelta(days=6), self.config.holiday_state):
            self.store.set_skipday(day, OVERRIDE)  # cook on a holiday after all
        else:
            self.store.remove_skipday(day)

    def _replan_day(self, day: date) -> WeekPlan | None:
        week_start = week_start_of(day)
        plan = self.store.plan(week_start)
        if plan is None:
            return None
        keep = {m.day: m for m in plan.meals if m.day != day}
        plan.meals = self._plan(week_start, keep=keep)
        self._save(plan)
        return plan

    def _save(self, plan: WeekPlan) -> None:
        """Every plan change ends here: leftovers follow their dish, and a collected list follows the dishes."""
        self._relink_leftovers(plan)
        with self._watch(plan.week_start):
            self.store.save_plan(plan)

    def _relink_leftovers(self, plan: WeekPlan) -> None:
        """A Reste-Tag eats the dish of the lunch before it. If that is no longer the same dish
        (swapped, made free, picked anew), the Reste-Tag is planned like any other day."""
        stale, prev = set(), None
        for m in sorted(plan.meals, key=lambda m: m.day):
            if m.skipped:
                continue
            if m.leftovers and (prev is None or prev.leftovers or prev.recipe_uid != m.recipe_uid):
                stale.add(m.day)
            prev = m
        if stale:
            keep = {m.day: m for m in plan.meals if m.day not in stale}
            plan.meals = self._plan(plan.week_start, keep=keep)

    def eaten(self, plan: WeekPlan) -> WeekPlan:
        """The plan as it was eaten: Nachträge replace or add meals."""
        end = plan.week_start + timedelta(days=6)
        cooked = self.store.cooked(plan.week_start, end)
        return replace(plan, meals=with_cooked(plan.meals, cooked, (plan.week_start, end))) if cooked else plan

    def eaten_plan(self, week_start: date) -> WeekPlan | None:
        plan = self.store.plan(week_start)
        return self.eaten(plan) if plan else None

    # Ratings -----------------------------------------------------------------

    def rate(self, recipe_uid: str, day: date, verdict: str, person: str = "") -> bool:
        """Rating after a meal: gut / mittel / schlecht, or "nie" (never plan again). Returns whether
        the dish just became Kinder-OK: a "gut" as its very first rating does that (and writes the
        category to Paprika). A Neu dish joins the rotation as before, without the announcement."""
        if verdict not in ("gut", "mittel", "schlecht", "nie"):
            raise ValueError("Bewertung: gut, mittel, schlecht oder nie")
        learned = False
        if verdict == "nie":
            self.store.set_feedback(recipe_uid, "nein")
            verdict = "schlecht"
        elif verdict == "gut":
            r = self.store.recipe(recipe_uid)
            if r and recipe_uid not in self.store.feedback():
                rated_before = any(x["uid"] == recipe_uid for x in self.store.ratings())
                if r.has_tag(self.config.new_tag):
                    self.store.set_feedback(recipe_uid, "ok")  # a new dish that went down well joins the rotation
                elif not r.has_tag(self.config.kid_tag) and not rated_before:
                    # only the very first rating teaches: a "gut" after "ging so", or after the family
                    # took Kinder-OK back, changes the weight and nothing else
                    learned = True
                    self.store.set_feedback(recipe_uid, "ok")  # counts at once, also if Paprika is away
                    self._write_kid_tag(recipe_uid, True)
        self.store.set_rating(recipe_uid, day, verdict, person)
        return learned

    def rate_day(self, day: date, verdict: str, person: str = "") -> str:
        """Rate what was eaten on `day` (the answer to „Wie war's?“). Returns the dish's name."""
        plan = self.eaten_plan(week_start_of(day))
        meal = next((m for m in plan.meals if m.day == day), None) if plan else None
        if meal is None or not meal.recipe_uid or meal.skipped or meal.leftovers:
            raise KeyError(f"Am {day:%d.%m.} gibt es kein Gericht zum Bewerten.")
        self.rate(meal.recipe_uid, day, verdict, person)
        return meal.name

    def _write_kid_tag(self, uid: str, on: bool) -> None:
        """Kinder-OK to Paprika in the background; best effort, the local Rückmeldung already counts."""
        if self.paprika is None:
            return

        previous = self._kid_thread

        def run() -> None:
            tag = [self.config.kid_tag]
            if previous is not None:
                previous.join()  # add, then take back: in the order the family did it
            try:
                self.edit_recipe_tags(uid, tag if on else [], [] if on else tag)
            except (PaprikaError, KeyError, RuntimeError, OSError) as e:
                logging.getLogger(__name__).warning("Kinder-OK für %s nicht nach Paprika geschrieben: %s", uid, e)

        self._kid_thread = threading.Thread(target=run, name="kinder-ok", daemon=True)
        self._kid_thread.start()

    def unlearn_kid_ok(self, recipe_uid: str) -> None:
        """Take back „jetzt Kinder-OK“ after a rating: the Rückmeldung goes, and the category in Paprika."""
        r = self.store.recipe(recipe_uid)
        if r is None or self.store.feedback().get(recipe_uid) != "ok" or r.has_tag(self.config.new_tag):
            raise ValueError("Das Rezept ist nicht über eine Bewertung Kinder-OK geworden.")
        self.store.remove_feedback(recipe_uid)
        self._write_kid_tag(recipe_uid, False)

    def rating_weights(self) -> dict[str, float]:
        """Chance multiplier per recipe from the family's ratings (1.0 = neutral, 0.4..1.6)."""
        scores: dict[str, list[int]] = {}
        for r in self.store.ratings():
            scores.setdefault(r["uid"], []).append({"gut": 1, "mittel": 0, "schlecht": -1}[r["verdict"]])
        return {uid: max(0.4, min(1.6, 1 + 0.6 * sum(s) / len(s))) for uid, s in scores.items()}

    def planning_recipes(self) -> list[Recipe]:
        """Recipes as the planner sees them: a confirmed cooking time fills in where Paprika has none."""
        times = self.store.cooktimes()
        return [
            replace(r, total_time=f"{times[r.uid]} Min.") if r.uid in times and minutes(r.total_time) is None else r
            for r in self.store.recipes()
        ]

    def season_of(self, month: int) -> dict[str, tuple[list[str], list[str]]]:
        """Per recipe: (produce in season, clearly seasonal produce out of season) in `month`;
        only recipes that name any. Rebuilt when the raw recipe cache changes."""
        cached = getattr(self, "_season", None)
        if cached is None or cached[0] != self.store.cache_generation:
            cached = self._season = (self.store.cache_generation, {})
        if month in cached[1]:
            return cached[1][month]
        out = {}
        for uid, (_, raw) in self.store.paprika_cache().items():
            found = season.seasonal(shopping.ingredient_lines(raw.get("ingredients") or ""), month)
            if found[0] or found[1]:
                out[uid] = found
        cached[1][month] = out
        return out

    def plan_weights(self, week_start: date) -> dict[str, float]:
        """Chance factor per recipe: the family's ratings times the season of that week."""
        weights = dict(self.rating_weights())
        for uid, (_ins, outs) in self.season_of((week_start + timedelta(days=2)).month).items():
            weights[uid] = weights.get(uid, 1.0) * (season.OUT_OF_SEASON if outs else season.IN_SEASON)
        return weights

    def _plan(self, week_start: date, keep=None, exclude=()) -> list:
        return plan_week(
            week_start,
            self.planning_recipes(),
            self.config,
            last_cooked=self.store.last_cooked(week_start),
            feedback=self.store.feedback(),
            rng=self.rng,
            keep=keep,
            exclude=exclude,
            skip=self.skip_map(week_start),
            weights=self.plan_weights(week_start),
            avoid=self.avoided_uids(),
            quick=self.store.quickdays(week_start, week_start + timedelta(days=6)),
            themes=self.store.themedays(week_start, week_start + timedelta(days=6)),
        )

    # Dislikes and quick days ---------------------------------------------------

    def dislikes(self) -> list[str]:
        """Ingredients the children don't like, as entered in the settings ("Pilze, Oliven")."""
        return [w.strip() for w in (self.store.meta("abneigungen") or "").split(",") if w.strip()]

    def save_dislikes(self, text: str) -> None:
        words = [w.strip() for w in text.replace("\n", ",").split(",") if w.strip()]
        self.store.set_meta("abneigungen", ", ".join(dict.fromkeys(words)))

    def ingredient_index(self) -> dict[str, list[str]]:
        """Ingredient names per recipe (no amounts, no headings), rebuilt when the raw cache changes."""
        key = self.store.cache_generation
        cached = getattr(self, "_ingredient_index", None)
        if cached and cached[0] == key:
            return cached[1]
        index = {}
        for uid, (_, raw) in self.store.paprika_cache().items():
            lines = shopping.ingredient_lines(raw.get("ingredients", ""))
            index[uid] = list(dict.fromkeys(shopping.parse_line(line)["name"] for line in lines))
        self._ingredient_index = (key, index)
        return index

    def avoided_uids(self) -> set[str]:
        """Recipes whose ingredients name a dislike. A word matches as itself, in a plural form or as
        the end of a compound ("Pilze" finds "Steinpilze"), never as its start: "Oliven" does not
        find "Olivenöl", and short words like "Ei" match only themselves ("Eier", not "eine")."""
        words = [w.casefold() for w in self.dislikes()]
        if not words:
            return set()
        key = (self.store.cache_generation, tuple(words))
        cached = getattr(self, "_avoided", None)
        if cached and cached[0] == key:
            return cached[1]
        forms = []
        for w in words:
            if len(w) <= 3:  # "Ei": only "ei" and "eier", never "ein" or "eis"
                forms.append(({w, w + "er"}, False))
                continue
            stem = w[:-1] if w.endswith(("n", "e", "s")) else w
            forms.append(({w, stem, stem + "n", stem + "en", stem + "e", stem + "s", stem + "er"}, True))
        out = set()
        for uid, names in self.ingredient_index().items():
            tokens = {t for n in names for t in re.findall(r"[a-zäöüß]+", n.casefold())}
            if any(t in f or (compound and any(t.endswith(x) for x in f)) for t in tokens for f, compound in forms):
                out.add(uid)
        self._avoided = (key, out)
        return out

    def _set_quick(self, day: date, on: bool) -> WeekPlan | None:
        """Mark a day as short on time. A draft keeps a dish that is already quick, otherwise picks a
        quick one; if the theme has none, the planned dish stays."""
        self.store.set_quickday(day, on)
        plan = self.store.plan(week_start_of(day))
        if plan is None or plan.status != "entwurf" or not on:
            return plan
        current = next((m for m in plan.meals if m.day == day), None)
        recipes = {r.uid: r for r in self.planning_recipes()}
        if (
            current is None
            or current.skipped
            or (current.recipe_uid in recipes and is_quick(recipes[current.recipe_uid]))
        ):
            return plan
        new = self._replan_day(day)
        picked = next((m for m in new.meals if m.day == day), None) if new else None
        if picked is None or picked.recipe_uid not in recipes or not is_quick(recipes[picked.recipe_uid]):
            self._save(plan)  # nothing quick for this theme: keep what was planned
            return plan
        return new

    def _generate(self, week_start: date, notify: bool = False, today: date | None = None) -> WeekPlan:
        """Plan the week anew. Dishes picked by hand stay, and so do their leftovers. An approved
        week is never re-rolled (Sous-Chef: only the family changes what is in the calendar)."""
        old = self.store.plan(week_start)
        if old and old.status == "freigegeben":
            raise ValueError("Die Woche ist freigegeben. Zum neu Planen erst „Wieder bearbeiten“.")
        keep = {m.day: m for m in old.meals if m.chosen} if old else {}
        for m in old.meals if old else []:
            if m.leftovers and any(k.recipe_uid == m.recipe_uid for k in keep.values()):
                keep[m.day] = m
        wished = self._wished(week_start, keep, today)
        keep.update(wished)
        plan = WeekPlan(week_start, "entwurf", self._plan(week_start, keep=keep))
        self._save(plan)
        for m in wished.values():
            self.store.set_wish(m.recipe_uid, False)
        if notify:
            self._notify(plan)
        return plan

    # Wunschliste ---------------------------------------------------------------

    def _wish_day(self, r: Recipe, days: list[date]) -> date | None:
        """The first of `days` whose theme the recipe has, else the first one."""
        fits = (d for d in days if self.theme_of(d) == ANY_THEME or r.has_tag(self.theme_of(d)))
        return next(fits, days[0] if days else None)

    def _wished(self, week_start: date, keep: dict[date, Meal], today: date | None = None) -> dict[date, Meal]:
        """Wished recipes for a coming week, each on a free planning day (its theme day if there is
        one), oldest wish first. They count as picked by hand."""
        today = today or date.today()
        if week_start <= today:  # the wish list is for the weeks ahead
            return {}
        skip = self.skip_map(week_start)
        days = [week_start + timedelta(days=rule.weekday) for rule in self.config.days]
        days = [d for d in days if d not in keep and d not in skip]
        taken = {m.recipe_uid for m in keep.values()}
        feedback = self.store.feedback()
        out: dict[date, Meal] = {}
        for uid in self.store.wishes():
            r = self.store.recipe(uid)
            if r is None or feedback.get(uid) == "nein" or uid in taken:
                self.store.set_wish(uid, False)  # gone from Paprika, ruled out, or already in the week
                continue
            day = self._wish_day(r, days)
            if day is None:
                break  # the week is full; the rest waits for the next one
            is_new = r.has_tag(self.config.new_tag) and uid not in feedback
            out[day] = Meal(day, uid, r.name, self.theme_of(day), is_new=is_new, reason="Gewünscht", chosen=True)
            days.remove(day)
        return out

    def wishes(self) -> list[Recipe]:
        known = {r.uid: r for r in self.store.recipes()}
        return [known[u] for u in self.store.wishes() if u in known]

    def add_wish(self, recipe_uid: str, today: date | None = None) -> str:
        """„Nächste Woche einplanen“: into next week's draft right away if there is one, else onto the
        wish list for the next draft. Returns what happened, for the family to read."""
        today = today or date.today()
        r = self.store.recipe(recipe_uid)
        if r is None:
            raise KeyError(f"Rezept {recipe_uid} nicht gefunden")
        if self.store.feedback().get(recipe_uid) == "nein":
            raise ValueError(f"{r.name} ist als „nie wieder“ markiert und wird nicht geplant.")
        week_start = week_start_of(today) + timedelta(weeks=1)
        plan = self.store.plan(week_start)
        if plan is not None and any(m.recipe_uid == recipe_uid and not m.leftovers for m in plan.meals):
            day = next(m.day for m in plan.meals if m.recipe_uid == recipe_uid)
            return f"{r.name} steht schon am {WEEKDAY_NAMES[day.weekday()]} im Plan."
        if plan is not None and plan.status == "entwurf":
            open_days = [m.day for m in plan.meals if not (m.chosen or m.skipped or m.leftovers)]
            day = self._wish_day(r, sorted(open_days))
            if day is not None:
                with self._undoable(week_start) as note:
                    self._choose(day, recipe_uid, today)
                    note["label"] = f"{r.name} am {self._wd(day)} eingeplant"
                return f"{r.name} steht jetzt am {WEEKDAY_NAMES[day.weekday()]} im Entwurf für nächste Woche."
        self.store.set_wish(recipe_uid, True)
        if plan is not None and plan.status == "freigegeben":
            return f"Die nächste Woche ist schon freigegeben. {r.name} kommt in den Entwurf danach."
        return f"{r.name} kommt in den nächsten Wochenentwurf."

    def remove_wish(self, recipe_uid: str) -> None:
        self.store.set_wish(recipe_uid, False)

    # Vorschläge für Thema und Kochzeit ----------------------------------------------

    def _proposal_candidates(self) -> list[tuple[Recipe, bool, bool]]:
        """Lunch dishes that lack a theme or a cooking time and were not asked about yet:
        (recipe, needs theme, needs time)."""
        asked = {(p["uid"], p["kind"]) for p in self.store.proposals(None)}
        times = self.store.cooktimes()
        feedback = self.store.feedback()
        out = []
        for r in sorted(self.store.recipes(), key=lambda r: r.name.casefold()):
            if not is_lunch(r, self.config) or feedback.get(r.uid) == "nein":
                continue
            theme = not any(r.has_tag(t) for t in self.config.themes) and (r.uid, "thema") not in asked
            timed = minutes(r.total_time) is not None or r.uid in times or r.has_tag("Schnell")
            need_time = not timed and (r.uid, "zeit") not in asked
            if theme or need_time:
                out.append((r, theme, need_time))
        return out

    def proposal_backlog(self) -> int:
        return len(self._proposal_candidates())

    def generate_proposals(self, progress=None) -> int:
        """Ask the LLM, a dozen recipes per request, and keep its proposals for the family to confirm."""
        client = self.llm()
        if client is None:
            raise LLMError("Kein LLM eingerichtet. In den Einstellungen Basis-URL, Schlüssel und Modell eintragen.")
        todo = self._proposal_candidates()
        themes = list(self.config.themes)
        index = self.ingredient_index()
        batches = [todo[i : i + proposals.BATCH] for i in range(0, len(todo), proposals.BATCH)]
        found, error = 0, None
        if progress:
            progress(0, len(batches))
        for n, batch in enumerate(batches):
            listing = [
                {"nr": i + 1, "name": r.name, "zutaten": index.get(r.uid, [])} for i, (r, _, _) in enumerate(batch)
            ]
            try:
                answer = client.chat_json(proposals.SYSTEM, proposals.build_prompt(listing, themes), temperature=0.1)
                parsed = proposals.parse_answer(answer, themes, len(batch))
            except LLMError as e:
                error, parsed = e, {}
            for i, (r, need_theme, need_time) in enumerate(batch):
                got = parsed.get(i + 1)
                if got is None:
                    continue
                if need_theme:
                    self.store.add_proposals([(r.uid, "thema", got["thema"])])
                    if not got["thema"]:  # no theme fits: remember, so it is not asked again
                        self.store.set_proposal(r.uid, "thema", "abgelehnt")
                    found += bool(got["thema"])
                if need_time:
                    self.store.add_proposals([(r.uid, "zeit", str(got["minuten"] or ""))])
                    if not got["minuten"]:  # the model named no time: do not ask again
                        self.store.set_proposal(r.uid, "zeit", "abgelehnt")
                    found += bool(got["minuten"])
            if progress:
                progress(n + 1, len(batches))
        if not found and error is not None:
            raise error
        return found

    def proposals_status(self) -> dict:
        with self._state_lock:
            return dict(self._proposal_state)

    def _start_proposal_job(self, work, total: int) -> dict:
        with self._state_lock:
            if self._proposal_state["laeuft"]:
                return dict(self._proposal_state)
            self._proposal_state = {"laeuft": True, "fertig": 0, "gesamt": total, "ergebnis": None, "fehler": None}
            state = dict(self._proposal_state)

        def progress(done: int, of: int) -> None:
            with self._state_lock:
                self._proposal_state.update(fertig=done, gesamt=of)

        def run() -> None:
            result: dict = {}
            try:
                result["ergebnis"] = work(progress)
            except (LLMError, PaprikaError, KeyError) as e:
                result["fehler"] = str(e.args[0]) if isinstance(e, KeyError) else str(e)
            except Exception as e:  # noqa: BLE001 - show it instead of a silently dead thread
                result["fehler"] = f"Unerwarteter Fehler: {e}"
                raise
            finally:
                with self._state_lock:
                    self._proposal_state.update(laeuft=False, **result)

        self._proposal_thread = threading.Thread(target=run, name="vorschlaege", daemon=True)
        self._proposal_thread.start()
        return state

    def start_proposals(self) -> dict:
        if self.llm() is None:
            raise LLMError("Kein LLM eingerichtet. In den Einstellungen Basis-URL, Schlüssel und Modell eintragen.")
        total = -(-len(self._proposal_candidates()) // proposals.BATCH)
        return self._start_proposal_job(self.generate_proposals, total)

    def open_proposals(self) -> list[dict]:
        """Open proposals per recipe: {"uid", "name", "thema", "minuten"} (either may be missing)."""
        known = {r.uid: r for r in self.store.recipes()}
        rows: dict[str, dict] = {}
        for p in self.store.proposals("offen"):
            r = known.get(p["uid"])
            if r is None:
                continue
            if p["kind"] == "thema" and any(r.has_tag(t) for t in self.config.themes):
                self.store.set_proposal(r.uid, "thema", None)  # got a theme by hand meanwhile
                continue
            row = rows.setdefault(r.uid, {"uid": r.uid, "name": r.name, "thema": "", "minuten": None})
            if p["kind"] == "thema":
                row["thema"] = p["value"]
            elif p["value"].isdigit():
                row["minuten"] = int(p["value"])
        return sorted(rows.values(), key=lambda x: x["name"].casefold())

    def answer_proposal(self, recipe_uid: str, kind: str, accept: bool) -> None:
        """Confirm or decline one proposal. A theme goes to Paprika as a category; a cooking time
        stays in Tischlein (Paprika is not filled with guessed times)."""
        self.open_proposals()  # drops what was settled by hand meanwhile
        p = next((x for x in self.store.proposals("offen") if x["uid"] == recipe_uid and x["kind"] == kind), None)
        if p is None:
            raise KeyError("Diesen Vorschlag gibt es nicht mehr.")
        if not accept:
            self.store.set_proposal(recipe_uid, kind, "abgelehnt")
            return
        if kind == "thema":
            self.edit_recipe_tags(recipe_uid, [p["value"]], [])
        else:
            self.store.set_cooktime(recipe_uid, int(p["value"]))
        self.store.set_proposal(recipe_uid, kind, None)

    def start_accept_proposals(self) -> dict:
        """Confirm every open proposal; the themes go to Paprika one by one in the background."""
        self.open_proposals()  # drops what was settled by hand meanwhile
        todo = [(p["uid"], p["kind"]) for p in self.store.proposals("offen")]
        todo.sort(key=lambda x: x[1] == "thema")  # times first: they need no Paprika
        if any(kind == "thema" for _, kind in todo) and self.paprika is None:
            raise PaprikaError("Zum Übernehmen der Themen muss Paprika verbunden sein.")

        def work(progress) -> int:
            for n, (uid, kind) in enumerate(todo):
                try:
                    self.answer_proposal(uid, kind, True)
                except KeyError:
                    pass  # answered meanwhile, or the recipe is gone
                progress(n + 1, len(todo))
            return len(todo)

        return self._start_proposal_job(work, len(todo))

    # Stöbern -----------------------------------------------------------------------

    def search_web(self, query: str, source: str = "chefkoch") -> list[dict]:
        """Browse Chefkoch (at least 4 stars, no paid recipes), Eat Smarter (lunch dishes) or Cookidoo
        (at least 4 stars). Results
        are not kept; what the family takes goes the way of every Web-Rezept. Raises ValueError when
        the search does not answer."""
        query = " ".join(query.split())[:80]
        if not query:
            return []
        site = webrecipe.SOURCES.get(source)
        if site is None:
            raise ValueError("Diese Quelle kennt Tischlein nicht.")
        try:
            if source == "cookidoo":
                results = self._search_cookidoo(query)
            elif source == "eatsmarter":
                body, _ = self.web_get(webrecipe.search_url(query, source), 2_000_000, ("text/html",))
                results = webrecipe.eatsmarter_results(body.decode("utf-8", "replace"))
            else:
                body, _ = self.web_get(webrecipe.search_url(query), 2_000_000, ("application/json",))
                results = webrecipe.search_results(json.loads(body))
        except (ValueError, TypeError) as e:
            raise ValueError(f"Die Suche bei {site} antwortet gerade nicht. Ein Rezept-Link geht weiterhin.") from e
        have = {webrecipe.normalize_url(r.source_url): r.uid for r in self.store.recipes() if r.source_url}
        for r in results:
            r["uid"] = have.get(webrecipe.normalize_url(r["url"]), "")
        return results

    def _search_cookidoo(self, query: str) -> list[dict]:
        """Cookidoo's search works in two steps: its search page hands out a key for the index, the
        index answers. The key is kept until shortly before it runs out; if the index refuses it
        earlier, it is fetched once more."""
        for attempt in (1, 2):
            key = self._cookidoo_key
            if key is None or key["until"] < time.time() + 300:
                page, _ = self.web_get(webrecipe.COOKIDOO_SEARCH, 2_000_000, ("text/html",))
                key = webrecipe.cookidoo_key(page.decode("utf-8", "replace"))
                if key is None:
                    raise ValueError("Cookidoo gibt keinen Suchschlüssel mehr heraus.")
                self._cookidoo_key = key
            try:
                body, _ = self.web_get(webrecipe.cookidoo_search_url(key, query), 2_000_000, ("application/json",))
            except ValueError:
                self._cookidoo_key = None
                if attempt == 2:
                    raise
                continue
            return webrecipe.cookidoo_results(json.loads(body))
        return []

    def _swap(self, week_start: date, day: date) -> WeekPlan:
        plan = self._require(week_start)
        if plan.status == "freigegeben":
            raise ValueError("Die Woche ist freigegeben. Für ein anderes Gericht erst „Wieder bearbeiten“.")
        current = next((m for m in plan.meals if m.day == day), None)
        if current is None:
            raise KeyError(f"Kein Tag {day} im Plan")
        keep = {m.day: m for m in plan.meals if m.day != day}
        plan.meals = self._plan(week_start, keep=keep, exclude={current.recipe_uid} if current.recipe_uid else set())
        if current.recipe_uid:
            plan.meals = [
                replace(current, reason="Keine Alternative zum Thema vorhanden")
                if m.day == day and m.recipe_uid is None
                else m
                for m in plan.meals
            ]
        self._save(plan)
        return plan

    # Rückgängig ------------------------------------------------------------------------

    @staticmethod
    def _fingerprint(state: dict) -> str:
        return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()

    @contextmanager
    def _undoable(self, week_start: date):
        """Remember the week as it was before a change, with the message the change sets in the
        yielded dict ("label"). One step per week. A failed change, or one that changed nothing,
        leaves the last step alone. The step also remembers the week as it was right after, so
        Rückgängig never throws away anything done later (portions, someone else's change)."""
        before = self.store.week_state(week_start)
        wishes = self.store.wishes()
        note = {"label": ""}
        yield note
        after = self.store.week_state(week_start)
        if note["label"] and after != before:
            record = {
                "label": note["label"],
                # wishes this change used up: Rückgängig puts them back on the list
                "wishes": [u for u in wishes if u not in self.store.wishes()],
                "at": datetime.now().isoformat(timespec="seconds"),
                "state": before,
                "after": self._fingerprint(after),
            }
            self.store.set_meta(f"rueckgaengig:{week_start.isoformat()}", json.dumps(record))

    def _undo_record(self, week_start: date) -> dict | None:
        """The last step, only while the week is still exactly as that change left it."""
        raw = self.store.meta(f"rueckgaengig:{week_start.isoformat()}")
        if not raw:
            return None
        record = json.loads(raw)
        if record.get("after") != self._fingerprint(self.store.week_state(week_start)):
            return None
        return record

    def last_change(self, week_start: date) -> dict | None:
        """The change "Rückgängig" would take back: {"label", "at"}, or None."""
        record = self._undo_record(week_start)
        return {"label": record["label"], "at": record["at"]} if record else None

    def undo(self, week_start: date) -> str:
        """Put the week back as it was before its last change. Returns what was taken back."""
        record = self._undo_record(week_start)
        if record is None:
            had_one = bool(self.store.meta(f"rueckgaengig:{week_start.isoformat()}"))
            raise ValueError(
                "Die Woche hat sich inzwischen geändert, das lässt sich nicht mehr zurücknehmen."
                if had_one
                else "Es gibt nichts zum Rückgängigmachen."
            )
        with self._watch(week_start):
            self.store.restore_week_state(week_start, record["state"])
        for uid in record.get("wishes", []):
            self.store.set_wish(uid, True)
        self.store.set_meta(f"rueckgaengig:{week_start.isoformat()}", "")
        return record["label"]

    @staticmethod
    def _wd(day: date) -> str:
        return WEEKDAY_NAMES[day.weekday()]

    @staticmethod
    def _dish(plan: WeekPlan | None, day: date) -> str:
        meal = next((m for m in plan.meals if m.day == day), None) if plan else None
        return meal.name if meal and meal.recipe_uid and not meal.skipped else ""

    def move(self, day: date, to: date, today: date | None = None) -> WeekPlan:
        with self._undoable(week_start_of(day)) as note:
            name = self._dish(self.store.plan(week_start_of(day)), day) or "Der freie Tag"
            plan = self._move(day, to, today)
            note["label"] = f"{name} auf {self._wd(to)} verschoben"
        return plan

    def swap(self, week_start: date, day: date) -> WeekPlan:
        with self._undoable(week_start) as note:
            plan = self._swap(week_start, day)
            note["label"] = f"{self._wd(day)}: jetzt {self._dish(plan, day) or 'nichts Passendes'}"
        return plan

    def choose(self, day: date, recipe_uid: str, today: date | None = None) -> WeekPlan:
        with self._undoable(week_start_of(day)) as note:
            plan = self._choose(day, recipe_uid, today)
            note["label"] = f"{self._dish(plan, day)} am {self._wd(day)}"
        return plan

    def set_theme(self, day: date, theme: str | None, today: date | None = None) -> WeekPlan | None:
        with self._undoable(week_start_of(day)) as note:
            plan = self._set_theme(day, theme, today)
            note["label"] = f"{self._wd(day)}: Thema {self.theme_of(day)}"
        return plan

    def set_leftovers(self, day: date, on: bool) -> WeekPlan:
        with self._undoable(week_start_of(day)) as note:
            plan = self._set_leftovers(day, on)
            nxt = self.next_lunch(plan, day)
            note["label"] = f"Reste am {self._wd(nxt.day)}" if on and nxt else "Kein Reste-Tag mehr"
        return plan

    def set_day_free(self, day: date, reason: str) -> WeekPlan | None:
        with self._undoable(week_start_of(day)) as note:
            plan = self._set_day_free(day, reason)
            note["label"] = f"{self._wd(day)}: kein Mittagessen"
        return plan

    def set_day_cooking(self, day: date) -> WeekPlan | None:
        with self._undoable(week_start_of(day)) as note:
            plan = self._set_day_cooking(day)
            note["label"] = f"{self._wd(day)}: doch kochen"
        return plan

    def set_quick(self, day: date, on: bool) -> WeekPlan | None:
        with self._undoable(week_start_of(day)) as note:
            plan = self._set_quick(day, on)
            note["label"] = f"{self._wd(day)}: wenig Zeit" if on else f"{self._wd(day)}: wieder normal"
        return plan

    def generate(self, week_start: date, notify: bool = False, today: date | None = None) -> WeekPlan:
        with self._undoable(week_start) as note:
            plan = self._generate(week_start, notify, today)
            note["label"] = "Woche neu geplant"
        return plan

    def set_status(self, week_start: date, status: str, person: str = "", notify: bool = True) -> WeekPlan:
        with self._undoable(week_start) as note:
            plan = self._set_status(week_start, status, person, notify)
            note["label"] = "Woche freigegeben" if status == "freigegeben" else "Woche wieder in Bearbeitung"
        return plan

    def log_cooked(
        self, day: date, recipe_uid: str | None = None, name: str = "", person: str = "", today: date | None = None
    ) -> None:
        with self._undoable(week_start_of(day)) as note:
            self._log_cooked(day, recipe_uid, name, person, today)
            note["label"] = f"Nachgetragen für {self._wd(day)}"

    def remove_cooked(self, day: date) -> None:
        with self._undoable(week_start_of(day)) as note:
            self._remove_cooked(day)
            note["label"] = "Nachtrag entfernt"

    # Moving, picking, themes, leftovers -------------------------------------------

    def theme_of(self, day: date) -> str:
        """The day's theme: a Themenwechsel for this week, else the weekly schedule."""
        override = self.store.themedays(day, day).get(day)
        rule = next((r for r in self.config.days if r.weekday == day.weekday()), None)
        return override or (rule.theme if rule else "")

    def theme_choices(self) -> list[str]:
        return [*self.config.themes, ANY_THEME]

    @staticmethod
    def _meal_on(plan: WeekPlan, day: date) -> Meal:
        meal = next((m for m in plan.meals if m.day == day), None)
        if meal is None:
            raise KeyError(f"Am {day:%d.%m.} ist kein Tag im Plan.")
        return meal

    @staticmethod
    def next_lunch(plan: WeekPlan, day: date) -> Meal | None:
        """The next day of the week with lunch after `day` (where its leftovers would go)."""
        return next((m for m in sorted(plan.meals, key=lambda m: m.day) if m.day > day and not m.skipped), None)

    def _move(self, day: date, to: date, today: date | None = None) -> WeekPlan:
        """Verschieben: the two days swap their dishes (a free day swaps too). The theme stays with the
        weekday; leftovers move with their dish to the lunch after its new day."""
        today = today or date.today()
        week_start = week_start_of(day)
        if week_start_of(to) != week_start or day == to:
            raise ValueError("Verschieben geht nur auf einen anderen Tag derselben Woche.")
        if min(day, to) < today:
            raise ValueError("Vergangene Tage lassen sich nicht verschieben.")
        plan = self._require(week_start)
        a, b = self._meal_on(plan, day), self._meal_on(plan, to)
        if a.leftovers or b.leftovers:
            raise ValueError("Ein Reste-Tag zieht mit seinem Gericht mit und lässt sich nicht selbst verschieben.")
        if a.skipped and b.skipped:
            return plan
        if a.skipped != b.skipped:  # the free day moves too
            free, cook = (day, to) if a.skipped else (to, day)
            reason = (a if a.skipped else b).reason
            self._mark_cooking(free)
            self.store.set_skipday(cook, reason)
        servings = self.store.servings(week_start, week_start + timedelta(days=6))

        def moved(m: Meal, dest: date, src: date) -> Meal:
            if m.skipped:
                return replace(m, day=dest, theme=self.theme_of(dest))
            why = f"Verschoben von {WEEKDAY_NAMES[src.weekday()]}"
            return replace(m, day=dest, theme=self.theme_of(dest), reason=why)

        owners = {m.recipe_uid for m in plan.meals if m.leftovers}
        movers = [m for m in (a, b) if m.recipe_uid in owners and not m.skipped]
        meals = {m.day: m for m in plan.meals if m.day not in (day, to)}
        for m in plan.meals:  # their old Reste-Tag is planned anew
            if m.leftovers and any(x.recipe_uid == m.recipe_uid for x in movers):
                meals.pop(m.day, None)
        meals[day], meals[to] = moved(b, day, to), moved(a, to, day)
        lunch_days = sorted(m.day for m in plan.meals if not meals.get(m.day, m).skipped)
        for m in movers:
            dish = meals[to if m is a else day]
            nxt = next((d for d in lunch_days if d > dish.day), None)
            if nxt is not None and not (nxt in meals and meals[nxt].leftovers):
                meals[nxt] = self._leftovers(dish, nxt)
        plan.meals = self._plan(week_start, keep=meals)
        self._save(plan)
        for src, dest, m in ((day, to, a), (to, day, b)):  # chosen portions go with the dish
            if m.recipe_uid and (src, m.recipe_uid) in servings:
                self.store.set_servings(dest, m.recipe_uid, servings[(src, m.recipe_uid)])
        return plan

    def _leftovers(self, dish: Meal, on: date) -> Meal:
        return Meal(
            on,
            dish.recipe_uid,
            f"Reste: {dish.name}",
            self.theme_of(on),
            reason=f"Reste von {WEEKDAY_NAMES[dish.day.weekday()]}",
            leftovers=True,
        )

    def _set_leftovers(self, day: date, on: bool) -> WeekPlan:
        """Reste-Tag: the next lunch day of the week eats what `day` cooks (no cooking, no shopping)."""
        plan = self._require(week_start_of(day))
        meal = self._meal_on(plan, day)
        if meal.skipped or meal.leftovers or not meal.recipe_uid:
            raise ValueError("Reste gibt es nur von einem geplanten Gericht.")
        nxt = self.next_lunch(plan, day)
        if on:
            if nxt is None:
                raise ValueError("Reste gibt es nur innerhalb der Woche.")
            plan.meals = [self._leftovers(meal, nxt.day) if m.day == nxt.day else m for m in plan.meals]
            self._save(plan)
            return plan
        if nxt is not None and nxt.leftovers:
            return self._replan_day(nxt.day) or plan
        return plan

    def has_leftovers(self, plan: WeekPlan, day: date) -> bool:
        nxt = self.next_lunch(plan, day)
        return bool(nxt and nxt.leftovers)

    def _set_theme(self, day: date, theme: str | None, today: date | None = None) -> WeekPlan | None:
        """Themenwechsel for one day of one week; None or the weekly theme goes back to the schedule.
        The day is planned anew right away (a free day or a Reste-Tag keeps what it has)."""
        today = today or date.today()
        if day < today:
            raise ValueError("Für vergangene Tage lässt sich das Thema nicht mehr ändern.")
        rule = next((r for r in self.config.days if r.weekday == day.weekday()), None)
        if rule is None:
            raise KeyError(f"Am {day:%d.%m.} wird nicht geplant.")
        if theme is not None and theme not in self.theme_choices():
            raise ValueError(f"Unbekanntes Thema: {theme}")
        self.store.set_themeday(day, None if theme in (None, rule.theme) else theme)
        plan = self.store.plan(week_start_of(day))
        meal = next((m for m in plan.meals if m.day == day), None) if plan else None
        if meal is None or meal.skipped or meal.leftovers:
            return plan
        return self._replan_day(day)

    def choice_hints(self, r: Recipe, day: date) -> list[str]:
        """Why the planner would not pick this dish on `day`; shown, never enforced (Rezept wählen)."""
        hints = []
        last = self.store.last_cooked(day).get(r.uid)
        if last and last > day - timedelta(weeks=self.config.cooldown_weeks):
            hints.append(f"zuletzt am {last:%d.%m.}")
        new = r.has_tag(self.config.new_tag) and r.uid not in self.store.feedback()
        if not new and not (r.has_tag(self.config.kid_tag) or self.store.feedback().get(r.uid) == "ok"):
            hints.append(f"nicht {self.config.kid_tag}")
        if r.uid in self.avoided_uids():
            hints.append("enthält eine Abneigung")
        return hints

    def recipe_choices(self, day: date, query: str = "", hints: bool = True) -> list[dict]:
        """Recipes for Rezept wählen / Nachtrag: the day's theme first, flagged ones last."""
        theme = self.theme_of(day)
        feedback = self.store.feedback()
        q = query.strip().casefold()
        cfg = self.config

        def themed(r: Recipe) -> bool:
            if theme == ANY_THEME:
                return is_lunch(r, cfg)
            return bool(theme) and r.has_tag(theme)

        plan = self.store.plan(week_start_of(day)) if hints else None
        elsewhere = {
            m.recipe_uid: WEEKDAY_NAMES[m.day.weekday()]
            for m in (plan.meals if plan else [])
            if m.recipe_uid and m.day != day and not m.leftovers
        }
        out = []
        for r in self.store.recipes():
            if feedback.get(r.uid) == "nein" or (q and q not in r.name.casefold()):
                continue
            lunch = is_lunch(r, cfg)
            flags = self.choice_hints(r, day) if hints else []
            if r.uid in elsewhere:  # picking it here plans that day anew
                flags.insert(0, f"schon am {elsewhere[r.uid]}")
            out.append(
                {
                    "uid": r.uid,
                    "name": r.name,
                    "themed": themed(r),
                    "lunch": lunch,
                    "new": r.has_tag(cfg.new_tag) and r.uid not in feedback,
                    "hints": flags,
                }
            )
        out.sort(key=lambda x: (bool(x["hints"]), not x["themed"], not x["lunch"], x["name"].casefold()))
        return out

    def _choose(self, day: date, recipe_uid: str, today: date | None = None) -> WeekPlan:
        """Rezept wählen: put this dish on `day`. It stays when the week is re-rolled; rules only show
        as hints. A new dish picked by hand fills the week's new slot."""
        today = today or date.today()
        if day < today:
            raise ValueError("Was an einem vergangenen Tag gekocht wurde, kommt als Nachtrag dazu.")
        week_start = week_start_of(day)
        plan = self._require(week_start)
        current = self._meal_on(plan, day)
        if current.skipped:
            raise ValueError("An einem freien Tag erst „Doch kochen“ wählen.")
        r = self.store.recipe(recipe_uid)
        if r is None:
            raise KeyError(f"Rezept {recipe_uid} nicht gefunden")
        is_new = r.has_tag(self.config.new_tag) and r.uid not in self.store.feedback()
        hints = self.choice_hints(r, day)
        meal = Meal(
            day,
            r.uid,
            r.name,
            self.theme_of(day),
            is_new=is_new,
            reason="; ".join(["Von Hand gewählt", *hints]),
            chosen=True,
        )
        keep = {
            m.day: m
            for m in plan.meals
            if m.day != day and m.recipe_uid != r.uid and not (is_new and m.is_new and not m.chosen)
        }
        keep[day] = meal
        plan.meals = self._plan(week_start, keep=keep)
        self._save(plan)
        return plan

    # Nachträge -------------------------------------------------------------------

    def _log_cooked(
        self, day: date, recipe_uid: str | None = None, name: str = "", person: str = "", today: date | None = None
    ) -> None:
        """Nachtrag: what was cooked on `day`, planned or not. A recipe from Paprika counts like any
        cooked meal (cooldown, rating); free text only shows in the review."""
        today = today or date.today()
        if day > today:
            raise ValueError("Nachtragen geht für heute und vergangene Tage.")
        if recipe_uid:
            r = self.store.recipe(recipe_uid)
            if r is None:
                raise KeyError(f"Rezept {recipe_uid} nicht gefunden")
            name = r.name
        name = name.strip()
        if not name:
            raise ValueError("Was gab es? Ein Rezept wählen oder einen Namen eintragen.")
        with self._watch(week_start_of(day)):
            self.store.set_cooked(day, recipe_uid or None, name[:120], person)

    def _remove_cooked(self, day: date) -> None:
        with self._watch(week_start_of(day)):
            self.store.remove_cooked(day)

    def _set_status(self, week_start: date, status: str, person: str = "", notify: bool = True) -> WeekPlan:
        plan = self._require(week_start)
        before = plan.status
        plan.status = status  # type: ignore[assignment]
        self._save(plan)
        if status == "freigegeben" and not self.has_shopping(week_start) and self._planned(week_start):
            self.refresh_shopping(week_start)  # the first list comes with the approval
        if status == "freigegeben" and before != "freigegeben" and self.webhook_url and notify:
            days = ", ".join(
                f"{WEEKDAY_NAMES[m.day.weekday()][:2]} {m.name}" for m in plan.meals if m.recipe_uid and not m.skipped
            )
            payload = {
                "titel": "Tischlein",
                "nachricht": f"{person or 'Jemand'} hat die Woche ab {week_start:%d.%m.} freigegeben: {days}.",
                "art": "freigabe",
                "von": person,
                "woche": week_start.isoformat(),
            }
            self._post_async(payload)
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
