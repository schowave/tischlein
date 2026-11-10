"""FastAPI app: web UI, JSON API for Home Assistant, ICS feed."""

from __future__ import annotations

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
from .service import Tischlein, from_env
from .views import (
    FREE_REASONS,
    ideas_view,
    recipe_json,
    recipes_view,
    review_view,
    settings_view,
    shopping_view,
    week_view,
)

HERE = Path(__file__).parent
# Set by the release build (Docker build arg); local runs show the package version.
VERSION = os.environ.get("TISCHLEIN_VERSION") or package_version("tischlein")
TEMPLATES = Jinja2Templates(directory=HERE / "templates")


class ShoppingItem(BaseModel):
    name: str
    menge: str = ""


class Done(BaseModel):
    erledigt: bool


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
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def week(which: str) -> date:
        try:
            return service.resolve_week(which)
        except ValueError:
            raise HTTPException(400, "Woche: 'aktuell', 'naechste' oder ein Datum (JJJJ-MM-TT)") from None

    def guard(fn, *args):
        try:
            return fn(*args)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e

    # Web UI ----------------------------------------------------------------

    def render(request: Request, template: str, ctx: dict):
        email = request.headers.get("cf-access-authenticated-user-email")
        person = service.person_name(email) if email else ""
        return TEMPLATES.TemplateResponse(request, template, {**ctx, "person": person})

    def person_of(request: Request) -> str:
        email = request.headers.get("cf-access-authenticated-user-email")
        return service.person_name(email) if email else ""

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, woche: str | None = None):
        v = week_view(service, woche, date.today())
        return render(request, "woche.html", {"v": v, "nav": "woche", "free_reasons": FREE_REASONS})

    @app.get("/rezepte", response_class=HTMLResponse)
    def recipes_page(request: Request):
        v = recipes_view(service)
        return render(request, "rezepte.html", {"v": v, "nav": "rezepte"})

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

    # Week: free days and ratings ---------------------------------------------

    @app.post("/tage/{day}/frei")
    def ui_day_free(day: date, grund: str = Form(""), woche: str | None = Form(None)):
        service.set_day_free(day, grund)
        return back(woche)

    @app.post("/tage/{day}/kochen")
    def ui_day_cook(day: date, woche: str | None = Form(None)):
        service.set_day_cooking(day)
        return back(woche)

    @app.post("/bewertung")
    def ui_rate(
        request: Request,
        rezept_uid: str = Form(...),
        tag: date = Form(...),
        urteil: str = Form(...),
        woche: str | None = Form(None),
    ):
        try:
            service.rate(rezept_uid, tag, urteil, person_of(request))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return back(woche)

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
    def api_shopping_add(which: str, body: ShoppingItem):
        if not body.name.strip():
            raise HTTPException(400, "Bitte etwas eintragen.")
        item_id = service.store.add_shopping_item(week(which), body.name.strip(), body.menge.strip())
        return {"id": item_id}

    @app.post("/api/einkauf/{item_id}/erledigt")
    def api_shopping_done(request: Request, item_id: int, body: Done):
        if not service.store.set_shopping_done(item_id, body.erledigt, person_of(request)):
            raise HTTPException(404, "Eintrag nicht gefunden")
        return {"ok": True, "von": person_of(request) if body.erledigt else ""}

    @app.post("/api/einkauf/{item_id}/loeschen")
    def api_shopping_delete(item_id: int):
        service.store.delete_shopping_item(item_id)
        return {"ok": True}

    # Ideas -------------------------------------------------------------------------

    @app.get("/ideen", response_class=HTMLResponse)
    def ideas_page(request: Request, hinweis: str | None = None):
        return render(request, "ideen.html", {"v": ideas_view(service), "nav": "ideen", "hinweis": hinweis})

    @app.post("/ideen/erzeugen")
    def ideas_generate():
        try:
            n = service.generate_ideas()
        except LLMError as e:
            return RedirectResponse(f"/ideen?hinweis={quote(str(e))}", status_code=303)
        msg = "" if n else "Das Modell hat keine brauchbaren Vorschläge geliefert. Bitte noch einmal versuchen."
        return RedirectResponse("/ideen" + (f"?hinweis={quote(msg)}" if msg else ""), status_code=303)

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

    # Review ------------------------------------------------------------------------

    @app.get("/rueckblick", response_class=HTMLResponse)
    def review_page(request: Request, wochen: int = 8):
        v = review_view(service, date.today(), max(1, min(wochen, 52)))
        return render(request, "rueckblick.html", {"v": v, "nav": "rueckblick"})

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

    def back(which: str | None = None) -> RedirectResponse:
        return RedirectResponse(f"/?woche={which}" if which in ("aktuell", "naechste") else "/", status_code=303)

    @app.post("/plaene/{which}/erzeugen")
    def ui_generate(which: str):
        service.generate(week(which))
        return back(which)

    @app.post("/plaene/{which}/tauschen")
    def ui_swap(which: str, tag: date = Form(...)):
        guard(service.swap, week(which), tag)
        return back(which)

    @app.post("/plaene/{which}/status")
    def ui_status(which: str, status: str = Form(...)):
        if status not in ("entwurf", "freigegeben"):
            raise HTTPException(400, "Status: entwurf oder freigegeben")
        guard(service.set_status, week(which), status)
        return back(which)

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
        return _plan_json(service.store.plan(week(woche)))

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
        body = _plan_json(service.generate(week(which), notify=True))
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

    @app.get("/healthz")
    def health():
        return {"ok": True, "version": VERSION}

    return app
