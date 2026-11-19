"""FastAPI app: web UI, JSON API for Home Assistant, ICS feed."""

from __future__ import annotations

import hashlib
import io
import os
import secrets
import zipfile
from contextlib import asynccontextmanager
from datetime import date, timedelta
from importlib.metadata import version as package_version
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import paprika
from .config import WEEKDAY_NAMES
from .llm import LLMError
from .models import WeekPlan
from .paprika_sync import PaprikaError
from .planner import week_start_of
from .service import Tischlein, from_env
from .views import (
    FREE_REASONS,
    choose_view,
    cook_view,
    ideas_view,
    recipe_json,
    recipes_view,
    review_view,
    settings_view,
    shopping_view,
    web_view,
    week_view,
)

HERE = Path(__file__).parent
# Set by the release build (Docker build arg); local runs show the package version.
VERSION = os.environ.get("TISCHLEIN_VERSION") or package_version("tischlein")
TEMPLATES = Jinja2Templates(directory=HERE / "templates")
_ASSET_MARKS: dict[str, tuple[int, str]] = {}  # file name -> (changed at, mark of its content)


def asset(name: str) -> str:
    """Address of a static file with a mark of its content. An edited file gets a new address, so
    no browser shows an old copy, also between two releases and while developing."""
    path = HERE / "static" / name
    changed = path.stat().st_mtime_ns
    known = _ASSET_MARKS.get(name)
    if known is None or known[0] != changed:
        known = (changed, hashlib.sha256(path.read_bytes()).hexdigest()[:10])
        _ASSET_MARKS[name] = known
    return f"/static/{name}?v={known[1]}"


class ShoppingItem(BaseModel):
    name: str
    menge: str = ""


class Done(BaseModel):
    erledigt: bool


class BringLists(BaseModel):
    listen: list[dict]


class BringState(BaseModel):
    liste: str = ""
    eintraege: list[dict] = []


class IdeasStart(BaseModel):
    thema: str = ""


class Restore(BaseModel):
    eintraege: list[dict] = []


class RatingIn(BaseModel):
    datum: date
    urteil: str
    von: str = ""


class ApproveIn(BaseModel):
    woche: date
    von: str = ""


class Wish(BaseModel):
    an: bool = True


class TagEdit(BaseModel):
    hinzufuegen: list[str] = []
    entfernen: list[str] = []


def _plan_json(plan: WeekPlan | None) -> dict:
    if plan is None:
        return {"status": "kein_plan", "mahlzeiten": []}
    return {
        "woche": plan.week_start.isoformat(),
        "status": plan.status,
        "mahlzeiten": [
            {
                "datum": m.day.isoformat(),
                "wochentag": WEEKDAY_NAMES[m.day.weekday()],
                "gericht": m.name,
                "thema": m.theme,
                "neu": m.is_new,
                "hinweis": m.reason,
                "rezept_uid": m.recipe_uid,
            }
            for m in plan.meals
        ],
    }


def create_app(service: Tischlein | None = None, auto_sync: bool | None = None) -> FastAPI:
    if auto_sync is None:
        auto_sync = service is None  # the real server checks Paprika by itself, tests do not
    if service is None:
        service = from_env()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if auto_sync:
            service.start_auto_sync()
        yield
        service.stop_auto_sync()

    app = FastAPI(title="Tischlein", lifespan=lifespan)
    app.state.service = service
    TEMPLATES.env.globals["version"] = VERSION
    TEMPLATES.env.globals["asset"] = asset
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def week(which: str) -> date:
        try:
            return service.resolve_week(which)
        except ValueError:
            raise HTTPException(400, "Woche: 'aktuell', 'naechste' oder ein Datum (JJJJ-MM-TT)") from None

    def guard(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    def to_day(woche: str | None, day: date, note: bool = False) -> RedirectResponse:
        r = back(woche, week_start_of(day) if note else None)
        r.headers["location"] += f"#tag-{day.isoformat()}"
        return r

    # Web UI ----------------------------------------------------------------

    def render(request: Request, template: str, ctx: dict):
        email = request.headers.get("cf-access-authenticated-user-email")
        person = service.person_name(email) if email else ""
        return TEMPLATES.TemplateResponse(request, template, {**ctx, "person": person})

    def person_of(request: Request) -> str:
        email = request.headers.get("cf-access-authenticated-user-email")
        return service.person_name(email) if email else ""

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, woche: str | None = None, meldung: str = "", gelernt: str = ""):
        v = week_view(service, woche, date.today(), toast=meldung, learned=gelernt)
        return render(request, "woche.html", {"v": v, "nav": "woche", "free_reasons": FREE_REASONS})

    @app.get("/rezepte", response_class=HTMLResponse)
    def recipes_page(request: Request, hinweis: str | None = None):
        v = recipes_view(service)
        return render(request, "rezepte.html", {"v": v, "nav": "rezepte", "hinweis": hinweis})

    @app.post("/vorschlaege/{uid}/{kind}")
    def proposal_answer(uid: str, kind: str, annehmen: int = Form(...)):
        if kind not in ("thema", "zeit"):
            raise HTTPException(404, "Unbekannter Vorschlag")
        try:
            service.answer_proposal(uid, kind, bool(annehmen))
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e
        except (PaprikaError, RuntimeError) as e:
            raise HTTPException(502, str(e)) from e
        return RedirectResponse("/rezepte#vorschlaege", status_code=303)

    @app.post("/api/vorschlaege/start")
    def api_proposals_start():
        try:
            return service.start_proposals()
        except LLMError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/vorschlaege/alle")
    def api_proposals_accept_all():
        try:
            return service.start_accept_proposals()
        except PaprikaError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/vorschlaege/status")
    def api_proposals_status():
        return service.proposals_status()

    @app.post("/api/rezepte/{uid}/wunsch")
    def api_wish(uid: str, body: Wish | None = None):
        if body is not None and not body.an:
            service.remove_wish(uid)
            return {"gewuenscht": False, "meldung": "Von der Wunschliste genommen."}
        text = guard(service.add_wish, uid)
        return {"gewuenscht": uid in service.store.wishes(), "meldung": text}

    @app.post("/wunsch/{uid}/entfernen")
    def wish_remove(uid: str, woche: str | None = Form(None)):
        service.remove_wish(uid)
        return back(woche)

    @app.post("/rezepte/{uid}/kinder-ok-zurueck")
    def kid_ok_undo(uid: str, woche: str | None = Form(None)):
        guard(service.unlearn_kid_ok, uid)
        return back(woche)

    def settings_page(request: Request, **extra):
        v = settings_view(service, str(request.base_url).rstrip("/"))
        return render(request, "einstellungen.html", {"v": v, "nav": "einstellungen", **extra})

    @app.get("/einstellungen", response_class=HTMLResponse)
    def settings(request: Request, ok: str | None = None):
        return settings_page(request, ok=ok)

    @app.post("/einstellungen/paprika", response_class=HTMLResponse)
    def settings_paprika(request: Request, email: str = Form(""), passwort: str = Form("")):
        try:
            service.connect_paprika(email, passwort)
        except (ValueError, PaprikaError) as e:
            return settings_page(request, paprika_error=str(e), email_input=email)
        service.start_sync()
        return RedirectResponse("/einstellungen?ok=paprika", status_code=303)

    @app.post("/einstellungen/llm", response_class=HTMLResponse)
    def settings_llm(request: Request, basis: str = Form(""), schluessel: str = Form(""), modell: str = Form("")):
        try:
            service.save_llm(basis, schluessel, modell)
        except (ValueError, LLMError) as e:
            return settings_page(request, llm_error=str(e), llm_input={"basis": basis, "modell": modell})
        return RedirectResponse("/einstellungen?ok=llm#llm", status_code=303)

    @app.post("/einstellungen/llm/entfernen")
    def settings_llm_clear():
        service.clear_llm()
        return RedirectResponse("/einstellungen?ok=llm-weg#llm", status_code=303)

    @app.post("/einstellungen/personen")
    async def settings_people(request: Request):
        form = await request.form()
        service.save_people({k.removeprefix("name:"): str(v) for k, v in form.items() if k.startswith("name:")})
        return RedirectResponse("/einstellungen?ok=personen#personen", status_code=303)

    @app.post("/einstellungen/bring")
    def settings_bring(liste: str = Form(""), abgleich: str = Form("")):
        try:
            service.set_bring_list(liste)
            service.set_bring_sync(abgleich == "1")
        except KeyError as e:
            raise HTTPException(400, e.args[0]) from e
        return RedirectResponse("/einstellungen?ok=bring#bring", status_code=303)

    @app.post("/einstellungen/abneigungen")
    def settings_dislikes(abneigungen: str = Form("")):
        service.save_dislikes(abneigungen)
        return RedirectResponse("/einstellungen?ok=abneigungen#abneigungen", status_code=303)

    # Week: free days and ratings ---------------------------------------------

    @app.post("/tage/{day}/frei")
    def ui_day_free(day: date, grund: str = Form(""), woche: str | None = Form(None)):
        service.set_day_free(day, grund)
        return to_day(woche, day, note=True)

    @app.post("/tage/{day}/portionen")
    def ui_day_servings(
        day: date, portionen: int = Form(...), woche: str | None = Form(None), ziel: str | None = Form(None)
    ):
        try:
            service.set_servings(day, portionen)
        except KeyError as e:
            raise HTTPException(404, e.args[0]) from e
        if ziel == "kochen":
            return RedirectResponse(f"/kochen/{day.isoformat()}", status_code=303)
        r = back(woche)
        r.headers["location"] += f"#tag-{day.isoformat()}"
        return r

    @app.post("/tage/{day}/schnell")
    def ui_day_quick(day: date, an: int = Form(...), woche: str | None = Form(None)):
        service.set_quick(day, bool(an))
        return to_day(woche, day, note=True)

    @app.get("/kochen/{day}", response_class=HTMLResponse)
    def cook_page(request: Request, day: date):
        try:
            v = cook_view(service, day)
        except KeyError as e:
            raise HTTPException(404, e.args[0]) from e
        return render(request, "kochen.html", {"v": v, "nav": "woche"})

    @app.post("/tage/{day}/kochen")
    def ui_day_cook(day: date, woche: str | None = Form(None)):
        service.set_day_cooking(day)
        return to_day(woche, day, note=True)

    # Week: moving, picking, themes, leftovers, Nachträge ----------------------------

    @app.post("/tage/{day}/verschieben")
    def ui_day_move(day: date, nach: date = Form(...), woche: str | None = Form(None)):
        guard(service.move, day, nach)
        return to_day(woche, nach, note=True)

    @app.post("/tage/{day}/thema")
    def ui_day_theme(day: date, thema: str = Form(""), woche: str | None = Form(None)):
        guard(service.set_theme, day, thema or None)
        return to_day(woche, day, note=True)

    @app.post("/tage/{day}/reste")
    def ui_day_leftovers(day: date, an: int = Form(...), woche: str | None = Form(None)):
        guard(service.set_leftovers, day, bool(an))
        return to_day(woche, day, note=True)

    @app.get("/tage/{day}/rezept", response_class=HTMLResponse)
    def choose_page(request: Request, day: date, q: str = ""):
        v = choose_view(service, day, "waehlen", q, date.today())
        return render(request, "waehlen.html", {"v": v, "nav": "woche"})

    @app.post("/tage/{day}/rezept")
    def ui_day_choose(day: date, rezept_uid: str = Form(...), woche: str | None = Form(None)):
        guard(service.choose, day, rezept_uid)
        return to_day(woche, day, note=True)

    @app.get("/nachtrag", response_class=HTMLResponse)
    def cooked_page(request: Request, tag: date | None = None, q: str = ""):
        v = choose_view(service, tag or date.today(), "nachtrag", q, date.today())
        return render(request, "waehlen.html", {"v": v, "nav": "woche"})

    @app.post("/nachtrag")
    def ui_log_cooked(
        request: Request,
        tag: date = Form(...),
        rezept_uid: str = Form(""),
        name: str = Form(""),
        zurueck: str = Form("/"),
    ):
        guard(service.log_cooked, tag, rezept_uid or None, name, person_of(request))
        target = zurueck if zurueck.startswith("/") and not zurueck.startswith("//") else "/"
        if target.startswith("/?") or target == "/":  # back on the week: show the change with Rückgängig
            path, _, anchor = target.partition("#")
            note = f"meldung={week_start_of(tag).isoformat()}"
            target = path + ("&" if "?" in path else "?") + note + (f"#{anchor}" if anchor else "")
        return RedirectResponse(target, 303)

    @app.post("/nachtrag/{day}/entfernen")
    def ui_remove_cooked(day: date, woche: str | None = Form(None)):
        service.remove_cooked(day)
        return to_day(woche, day, note=True)

    @app.post("/bewertung")
    def ui_rate(
        request: Request,
        rezept_uid: str = Form(...),
        tag: date = Form(...),
        urteil: str = Form(...),
        woche: str | None = Form(None),
    ):
        try:
            learned = service.rate(rezept_uid, tag, urteil, person_of(request))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        r = back(woche)
        if learned:  # say so, with a way back
            r.headers["location"] += ("&" if "?" in r.headers["location"] else "?") + f"gelernt={quote(rezept_uid)}"
        return r

    # Shopping list ---------------------------------------------------------------

    @app.get("/einkauf", response_class=HTMLResponse)
    def shopping_page(request: Request, woche: str = "aktuell", hinweis: str | None = None):
        v = shopping_view(service, week(woche), woche)
        return render(request, "einkauf.html", {"v": v, "nav": "einkauf", "hinweis": hinweis})

    @app.post("/einkauf/{which}/erzeugen")
    def shopping_generate(which: str):
        try:
            how = service.generate_shopping(week(which))
        except KeyError as e:
            return RedirectResponse(f"/einkauf?woche={which}&hinweis={quote(str(e.args[0]))}", status_code=303)
        note = "" if how in ("llm", "einfach") else how
        return RedirectResponse(
            f"/einkauf?woche={which}" + (f"&hinweis={quote(note)}" if note else ""), status_code=303
        )

    @app.get("/api/einkauf")
    def api_shopping(woche: str = "aktuell"):
        return service.store.shopping(week(woche))

    @app.post("/api/einkauf/{which}/eintrag")
    def api_shopping_add(request: Request, which: str, body: ShoppingItem):
        if not body.name.strip():
            raise HTTPException(400, "Bitte etwas eintragen.")
        item_id = service.add_shopping_item(week(which), body.name.strip(), body.menge.strip(), person_of(request))
        return {"id": item_id}

    @app.post("/api/einkauf/{which}/alle-erledigt")
    def api_shopping_all_done(request: Request, which: str, body: Done):
        n = service.set_all_done(week(which), body.erledigt, person_of(request))
        return {"geaendert": n}

    @app.post("/api/bring/listen")
    def api_bring_lists(body: BringLists):
        """Home Assistant reports its Bring lists here (rest_command, hourly)."""
        return {"listen": service.save_bring_lists(body.listen)}

    @app.get("/api/bring/ziel")
    def api_bring_target():
        """Which Bring list Home Assistant should read for the sync ("" = its default)."""
        return {"liste": service.bring_list(), "abgleich": service.bring_sync_on()}

    @app.post("/api/bring/stand")
    def api_bring_state(body: BringState):
        """Home Assistant reports the Bring list (todo.get_items); ticks and new items come over."""
        if not service.bring_sync_on():
            return {"abgehakt": 0, "neu": 0}
        return service.apply_bring_state(body.liste, body.eintraege)

    @app.post("/api/einkauf/{which}/bring")
    def api_shopping_bring(request: Request, which: str):
        try:
            return {"gesendet": service.send_to_bring(week(which), person_of(request))}
        except LookupError as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/api/einkauf/{which}/erledigte-entfernen")
    def api_shopping_remove_done(which: str):
        removed = service.remove_done(week(which))
        return {"entfernt": len(removed), "eintraege": removed}

    @app.post("/api/einkauf/{which}/wiederherstellen")
    def api_shopping_restore(which: str, body: Restore):
        return {"wiederhergestellt": guard(service.restore_items, week(which), body.eintraege)}

    @app.post("/api/einkauf/{item_id}/erledigt")
    def api_shopping_done(request: Request, item_id: int, body: Done):
        if not service.set_item_done(item_id, body.erledigt, person_of(request)):
            raise HTTPException(404, "Eintrag nicht gefunden")
        return {"ok": True, "von": person_of(request) if body.erledigt else ""}

    @app.post("/api/einkauf/{item_id}/loeschen")
    def api_shopping_delete(item_id: int):
        return {"ok": True, "eintraege": service.delete_item(item_id)}

    # Ideas -------------------------------------------------------------------------

    @app.get("/ideen", response_class=HTMLResponse)
    def ideas_page(request: Request, hinweis: str | None = None, suche: str = "", quelle: str = ""):
        v = ideas_view(service, suche, date.today(), quelle)
        return render(request, "ideen.html", {"v": v, "nav": "rezepte", "hinweis": hinweis})

    @app.post("/ideen/erzeugen")
    def ideas_generate(thema: str = Form("")):
        try:
            n = service.generate_ideas(3 if thema else 6, theme=thema or None)
        except LLMError as e:
            return RedirectResponse(f"/ideen?hinweis={quote(str(e))}", status_code=303)
        msg = "" if n else "Das Modell hat keine brauchbaren Vorschläge geliefert. Bitte noch einmal versuchen."
        return RedirectResponse("/ideen" + (f"?hinweis={quote(msg)}" if msg else ""), status_code=303)

    @app.post("/api/ideen/start")
    def api_ideas_start(body: IdeasStart | None = None):
        theme = body.thema if body else ""
        try:
            return service.start_ideas(3 if theme else 6, theme or None)
        except LLMError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/ideen/status")
    def api_ideas_status():
        return service.ideas_status()

    @app.post("/ideen/{idea_id}/uebernehmen")
    def ideas_accept(idea_id: int):
        try:
            recipe = service.accept_idea(idea_id)
        except (KeyError, PaprikaError) as e:
            text = e.args[0] if isinstance(e, KeyError) else str(e)
            return RedirectResponse(f"/ideen?hinweis={quote(text)}", status_code=303)
        return RedirectResponse(f"/rezepte?rezept={quote(recipe.uid)}", status_code=303)

    @app.post("/ideen/{idea_id}/verwerfen")
    def ideas_dismiss(idea_id: int):
        service.dismiss_idea(idea_id)
        return RedirectResponse("/ideen", status_code=303)

    # Web-Rezepte -------------------------------------------------------------------

    @app.get("/rezept-holen", response_class=HTMLResponse)
    def web_page(request: Request, url: str = "", text: str = ""):
        # the iOS share sheet may hand over the shared text instead of the bare link
        v = web_view(service, url or text)
        return render(request, "rezept-holen.html", {"v": v, "nav": "rezepte"})

    @app.post("/rezept-holen")
    def web_accept(url: str = Form(...), kategorien: list[str] = Form([]), einplanen: int = Form(0)):
        try:
            recipe = service.accept_web_recipe(url, kategorien)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except PaprikaError as e:
            raise HTTPException(502, str(e)) from e
        note = f"&hinweis={quote(service.add_wish(recipe.uid))}" if einplanen else ""
        return RedirectResponse(f"/rezepte?rezept={quote(recipe.uid)}{note}", status_code=303)

    # Review ------------------------------------------------------------------------

    @app.get("/rueckblick", response_class=HTMLResponse)
    def review_page(request: Request, wochen: int = 8):
        v = review_view(service, date.today(), max(1, min(wochen, 52)))
        return render(request, "rueckblick.html", {"v": v, "nav": "woche"})

    @app.post("/einstellungen/paprika/trennen")
    def settings_paprika_disconnect():
        service.disconnect_paprika()
        return RedirectResponse("/einstellungen?ok=getrennt", status_code=303)

    @app.post("/einstellungen/kalender", response_class=HTMLResponse)
    def settings_calendar(request: Request, name: str = Form(""), uhrzeit: str = Form(""), minuten: int = Form(45)):
        try:
            service.save_calendar_settings(name, uhrzeit, minuten)
        except ValueError as e:
            return settings_page(request, calendar_error=str(e))
        return RedirectResponse("/einstellungen?ok=kalender#kalender", status_code=303)

    @app.post("/einstellungen/kalender/neu")
    def settings_calendar_token():
        service.renew_calendar_token()
        return RedirectResponse("/einstellungen?ok=link#kalender", status_code=303)

    def back(which: str | None = None, note: date | None = None) -> RedirectResponse:
        """Back to the week; `note` (the changed week's Monday) shows that change with its Rückgängig."""
        query = [f"woche={which}"] if which in ("aktuell", "naechste") else []
        query += [f"meldung={note.isoformat()}"] if note else []
        return RedirectResponse("/" + ("?" + "&".join(query) if query else ""), status_code=303)

    @app.post("/plaene/{which}/erzeugen")
    def ui_generate(which: str):
        plan = service.store.plan(week(which))
        guard(service.generate, week(which))
        return back(which, week(which) if plan is not None else None)

    @app.post("/plaene/{which}/tauschen")
    def ui_swap(which: str, tag: date = Form(...)):
        guard(service.swap, week(which), tag)
        return to_day(which, tag, note=True)

    @app.post("/plaene/{which}/rueckgaengig")
    def ui_undo(which: str):
        guard(service.undo, week(which))
        return back(which)

    @app.post("/plaene/{which}/status")
    def ui_status(request: Request, which: str, status: str = Form(...)):
        if status not in ("entwurf", "freigegeben"):
            raise HTTPException(400, "Status: entwurf oder freigegeben")
        guard(service.set_status, week(which), status, person_of(request))
        return back(which, week(which))

    @app.post("/rueckmeldung")
    def ui_feedback(rezept_uid: str = Form(...), urteil: str = Form(...), woche: str | None = Form(None)):
        if urteil not in ("ok", "nein"):
            raise HTTPException(400, "Urteil: ok oder nein")
        service.feedback(rezept_uid, urteil)  # type: ignore[arg-type]
        return back(woche)

    @app.post("/sync")
    def ui_sync(zurueck: str = Form("/")):
        # without JavaScript: start in the background and return; the banner shows the progress
        if service.paprika is not None:
            service.start_sync()
        target = zurueck if zurueck in ("/", "/rezepte", "/einstellungen") else "/"
        return RedirectResponse(target, status_code=303)

    @app.post("/import")
    async def ui_import(datei: UploadFile):
        try:
            entries = paprika.read_export_raw(io.BytesIO(await datei.read()))
        except zipfile.BadZipFile, ValueError, KeyError:
            raise HTTPException(400, "Keine gültige .paprikarecipes-Datei") from None
        service.store.replace_recipes([r for r, _ in entries])
        # raw data gives ingredients for the recipe page and the shopping list
        service.store.replace_paprika_cache({r.uid: (raw.get("hash", ""), raw) for r, raw in entries})
        return back()

    # API -------------------------------------------------------------------

    @app.get("/api/rezepte")
    def api_recipes():
        return recipes_view(service)["data"]

    @app.get("/api/rezepte/{uid}")
    def api_recipe(uid: str):
        return guard(service.recipe_details, uid)

    @app.get("/bilder/{uid}.jpg")
    def photo(uid: str):
        path = service.recipe_photo(uid)
        if path is None:
            raise HTTPException(404, "Kein Bild")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=604800"})

    @app.post("/api/rezepte/{uid}/papierkorb")
    def api_trash(uid: str):
        try:
            service.set_recipe_trashed(uid, True)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e
        except PaprikaError as e:
            raise HTTPException(502, str(e)) from e
        return {"ok": True}

    @app.post("/api/rezepte/{uid}/wiederherstellen")
    def api_restore(uid: str):
        try:
            r = service.set_recipe_trashed(uid, False)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e
        except PaprikaError as e:
            raise HTTPException(502, str(e)) from e
        return recipe_json(service, r, service.store.feedback())

    @app.post("/api/rezepte/{uid}/tags")
    def api_recipe_tags(uid: str, body: TagEdit):
        try:
            r = service.edit_recipe_tags(uid, body.hinzufuegen, body.entfernen)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e
        except (PaprikaError, RuntimeError) as e:
            raise HTTPException(502, str(e)) from e
        return recipe_json(service, r, service.store.feedback())

    @app.get("/api/plan")
    def api_plan(woche: str = "aktuell"):
        # as eaten: a Nachtrag replaces the planned dish of its day, as in calendar and /api/heute
        return _plan_json(service.eaten_plan(week(woche)))

    @app.post("/api/bewertung")
    def api_rate(body: RatingIn):
        """The answer to „Wie war's?“ from Home Assistant: rates what was eaten that day."""
        return {"ok": True, "gericht": guard(service.rate_day, body.datum, body.urteil, body.von)}

    @app.post("/api/plaene/freigeben")
    def api_approve(body: ApproveIn):
        """„Freigeben“ from the notification: approves the draft of that week."""
        # no "… hat freigegeben" to everyone: Home Assistant clears the notification on all phones
        plan = guard(service.set_status, week_start_of(body.woche), "freigegeben", body.von, notify=False)
        return _plan_json(plan)

    @app.post("/api/sync/start")
    def api_sync_start():
        try:
            return service.start_sync()
        except PaprikaError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/api/sync/status")
    def api_sync_status():
        return service.sync_status()

    @app.post("/api/sync")
    def api_sync():
        try:
            return {"ok": True, "ergebnis": service.sync_paprika()}
        except PaprikaError as e:
            raise HTTPException(502, str(e)) from e

    @app.post("/api/plaene/{which}/erzeugen")
    def api_generate(which: str):
        sync_error = service.try_sync_paprika()
        try:
            body = _plan_json(service.generate(week(which), notify=True))
        except ValueError as e:  # approved: Home Assistant gets the plan as it stands, not an error
            body = {**_plan_json(service.store.plan(week(which))), "hinweis": str(e)}
        if sync_error:
            body["sync_fehler"] = sync_error
        return body

    @app.get("/api/heute")
    def api_today():
        return service.day_info(date.today())

    @app.get("/api/morgen")
    def api_tomorrow():
        return service.day_info(date.today() + timedelta(days=1))

    @app.get("/kalender.ics")
    def calendar():
        return Response(service.calendar_ics(), media_type="text/calendar; charset=utf-8")

    @app.get("/kalender/{token}.ics")
    def calendar_secret(token: str):
        if not secrets.compare_digest(token, service.calendar_settings()["token"]):
            raise HTTPException(404, "Unbekannter Kalender-Link")
        return Response(service.calendar_ics(), media_type="text/calendar; charset=utf-8")

    @app.get("/sw.js")
    def service_worker():
        # served from the root so it may handle /einkauf; never cached itself, so updates arrive
        return FileResponse(
            HERE / "static" / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/healthz")
    def health():
        return {"ok": True, "version": VERSION}

    return app
