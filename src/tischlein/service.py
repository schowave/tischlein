"""Use cases shared by web app and CLI: glue between store, planner and config."""

from __future__ import annotations

import json
import os
import random
import secrets
import threading
import time
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from . import ics, ideas, shopping
from .config import PlanConfig
from .holidays import holidays_between
from .llm import LLMClient, LLMError
from .models import Recipe, Verdict, WeekPlan
from .paprika import recipe_from_json
from .paprika_sync import CachedRecipe, PaprikaClient, PaprikaError
from .planner import plan_week, week_start_of
from .prep import prep_hints
from .store import Store
from .tagging import Change, apply_changes

OVERRIDE = "-"  # skipdays marker: cook on this holiday after all


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
        self._sync_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._sync_state = {"laeuft": False, "phase": "", "fertig": 0, "gesamt": 0, "ergebnis": None, "fehler": None}
        self._sync_thread: threading.Thread | None = None
        self._ideas_state = {"laeuft": False, "fertig": 0, "gesamt": 0, "ergebnis": None, "fehler": None}
        self._ideas_thread: threading.Thread | None = None
        self._counters_after_sync: str | None = None
        self._auto_thread: threading.Thread | None = None
        self._auto_stop = threading.Event()
        self._llm_transport = None  # tests inject a fake transport
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

    def _week_recipes(self, week_start: date) -> list[dict]:
        plan = self.store.plan(week_start)
        out = []
        for m in plan.meals if plan else []:
            entry = self.store.raw_recipe(m.recipe_uid) if m.recipe_uid and not m.skipped else None
            if entry:
                out.append({"name": m.name, "ingredients": entry[1].get("ingredients", "")})
        return out

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
            result = shopping.fallback_list(items)
        self.store.replace_generated_shopping(week_start, result)
        return how

    # Ideas ---------------------------------------------------------------------

    def generate_ideas(self, count: int = 6, progress=None) -> int:
        """Ask the LLM for `count` ideas, one dish per request.

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
        focus = ideas.focus_themes(supply, themes)
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

    def start_ideas(self, count: int = 6) -> dict:
        """Generate ideas in a background thread (several LLM calls take a minute) and return the state."""
        if self.llm() is None:
            raise LLMError("Kein LLM eingerichtet. In den Einstellungen Basis-URL, Schlüssel und Modell eintragen.")
        with self._state_lock:
            if self._ideas_state["laeuft"]:
                return dict(self._ideas_state)
            self._ideas_state = {"laeuft": True, "fertig": 0, "gesamt": count, "ergebnis": None, "fehler": None}
            state = dict(self._ideas_state)
        self._ideas_thread = threading.Thread(
            target=self._run_background_ideas, args=(count,), name="llm-ideas", daemon=True
        )
        self._ideas_thread.start()
        return state

    def _run_background_ideas(self, count: int) -> None:
        def progress(done: int, total: int) -> None:
            with self._state_lock:
                self._ideas_state.update(fertig=done, gesamt=total)

        result: dict = {}
        try:
            result["ergebnis"] = self.generate_ideas(count, progress=progress)
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

    def dismiss_idea(self, idea_id: int) -> None:
        self.store.set_idea_status(idea_id, "verworfen")

    # Preparation and reminder --------------------------------------------------------

    def day_info(self, day: date, approved_only: bool = False) -> dict:
        """What is for lunch on `day`, with preparation hints (for Home Assistant)."""
        plan = self.store.plan(week_start_of(day))
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

    def add_shopping_item(self, week_start: date, name: str, amount: str = "", person: str = "") -> int:
        """Put something on the list by hand and tell Home Assistant (push to the phones)."""
        item_id = self.store.add_shopping_item(week_start, name, amount)
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
            # in the background: the list must not wait for Home Assistant
            threading.Thread(target=self._post_webhook, args=(payload,), name="ha-webhook", daemon=True).start()
        return item_id

    def _post_webhook(self, payload: dict) -> None:
        req = urllib.request.Request(
            self.webhook_url, json.dumps(payload).encode(), {"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(req, timeout=5).close()
        except OSError:
            pass  # best effort

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
                    self.reminder_tick()
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

    def set_day_free(self, day: date, reason: str) -> WeekPlan | None:
        self.store.set_skipday(day, reason.strip() or "Kein Mittagessen")
        return self._replan_day(day)

    def set_day_cooking(self, day: date) -> WeekPlan | None:
        week_start = week_start_of(day)
        if day in holidays_between(week_start, week_start + timedelta(days=6), self.config.holiday_state):
            self.store.set_skipday(day, OVERRIDE)  # cook on a holiday after all
        else:
            self.store.remove_skipday(day)
        return self._replan_day(day)

    def _replan_day(self, day: date) -> WeekPlan | None:
        week_start = week_start_of(day)
        plan = self.store.plan(week_start)
        if plan is None:
            return None
        keep = {m.day: m for m in plan.meals if m.day != day}
        plan.meals = self._plan(week_start, keep=keep)
        self.store.save_plan(plan)
        return plan

    # Ratings -----------------------------------------------------------------

    def rate(self, recipe_uid: str, day: date, verdict: str, person: str = "") -> None:
        """Rating after a meal: gut / mittel / schlecht, or "nie" (never plan again)."""
        if verdict not in ("gut", "mittel", "schlecht", "nie"):
            raise ValueError("Bewertung: gut, mittel, schlecht oder nie")
        if verdict == "nie":
            self.store.set_feedback(recipe_uid, "nein")
            verdict = "schlecht"
        elif verdict == "gut":
            r = self.store.recipe(recipe_uid)
            if r and r.has_tag(self.config.new_tag) and recipe_uid not in self.store.feedback():
                self.store.set_feedback(recipe_uid, "ok")  # a new dish that went down well joins the rotation
        self.store.set_rating(recipe_uid, day, verdict, person)

    def rating_weights(self) -> dict[str, float]:
        """Chance multiplier per recipe from the family's ratings (1.0 = neutral, 0.4..1.6)."""
        scores: dict[str, list[int]] = {}
        for r in self.store.ratings():
            scores.setdefault(r["uid"], []).append({"gut": 1, "mittel": 0, "schlecht": -1}[r["verdict"]])
        return {uid: max(0.4, min(1.6, 1 + 0.6 * sum(s) / len(s))) for uid, s in scores.items()}

    def _plan(self, week_start: date, keep=None, exclude=()) -> list:
        return plan_week(
            week_start,
            self.store.recipes(),
            self.config,
            last_cooked=self.store.last_cooked(week_start),
            feedback=self.store.feedback(),
            rng=self.rng,
            keep=keep,
            exclude=exclude,
            skip=self.skip_map(week_start),
            weights=self.rating_weights(),
        )

    def generate(self, week_start: date, notify: bool = False) -> WeekPlan:
        meals = self._plan(week_start)
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
        plan.meals = self._plan(week_start, keep=keep, exclude={current.recipe_uid} if current.recipe_uid else set())
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
