"""FastAPI app: web UI, JSON API for Home Assistant, ICS feed."""

from __future__ import annotations

import io
import secrets
import zipfile
from datetime import date
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import ics, paprika
from .config import WEEKDAY_NAMES
from .models import WeekPlan
from .paprika_sync import PaprikaError
from .service import Tischlein, from_env
from .views import recipe_json, recipes_view, settings_view, week_view

HERE = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=HERE / "templates")


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
            {"datum": m.day.isoformat(), "wochentag": WEEKDAY_NAMES[m.day.weekday()], "gericht": m.name,
             "thema": m.theme, "neu": m.is_new, "hinweis": m.reason, "rezept_uid": m.recipe_uid}
            for m in plan.meals
        ],
    }


def create_app(service: Tischlein | None = None) -> FastAPI:
    if service is None:
        service = from_env()
    app = FastAPI(title="Tischlein")
    app.state.service = service
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def week(which: str) -> date:
        try:
            return service.resolve_week(which)
        except ValueError:
            raise HTTPException(400, "Woche: 'aktuell', 'naechste' oder ein Datum (JJJJ-MM-TT)")

    def guard(fn, *args):
        try:
            return fn(*args)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0]))

    # Web UI ----------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, woche: str | None = None):
        v = week_view(service, woche, date.today())
        return TEMPLATES.TemplateResponse(request, "woche.html", {"v": v, "nav": "woche"})

    @app.get("/rezepte", response_class=HTMLResponse)
    def recipes_page(request: Request):
        v = recipes_view(service)
        return TEMPLATES.TemplateResponse(request, "rezepte.html", {"v": v, "nav": "rezepte"})

    def settings_page(request: Request, **extra):
        v = settings_view(service, str(request.base_url).rstrip("/"))
        return TEMPLATES.TemplateResponse(request, "einstellungen.html", {"v": v, "nav": "einstellungen", **extra})

    @app.get("/einstellungen", response_class=HTMLResponse)
    def settings(request: Request, ok: str | None = None):
        return settings_page(request, ok=ok)

    @app.post("/einstellungen/paprika", response_class=HTMLResponse)
    def settings_paprika(request: Request, tasks: BackgroundTasks, email: str = Form(""), passwort: str = Form("")):
        try:
            service.connect_paprika(email, passwort)
        except (ValueError, PaprikaError) as e:
            return settings_page(request, paprika_error=str(e), email_input=email)
        tasks.add_task(service.try_sync_paprika)
        return RedirectResponse("/einstellungen?ok=paprika", status_code=303)

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
        service.try_sync_paprika()
        target = zurueck if zurueck in ("/", "/rezepte", "/einstellungen") else "/"
        return RedirectResponse(target, status_code=303)

    @app.post("/import")
    async def ui_import(datei: UploadFile):
        try:
            recipes = paprika.read_export(io.BytesIO(await datei.read()))
        except (zipfile.BadZipFile, ValueError, KeyError):
            raise HTTPException(400, "Keine gültige .paprikarecipes-Datei")
        service.store.replace_recipes(recipes)
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
            raise HTTPException(404, str(e.args[0]))
        except PaprikaError as e:
            raise HTTPException(502, str(e))
        return {"ok": True}

    @app.post("/api/rezepte/{uid}/wiederherstellen")
    def api_restore(uid: str):
        try:
            r = service.set_recipe_trashed(uid, False)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0]))
        except PaprikaError as e:
            raise HTTPException(502, str(e))
        return recipe_json(service, r, service.store.feedback())

    @app.post("/api/rezepte/{uid}/tags")
    def api_recipe_tags(uid: str, body: TagEdit):
        try:
            r = service.edit_recipe_tags(uid, body.hinzufuegen, body.entfernen)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0]))
        except (PaprikaError, RuntimeError) as e:
            raise HTTPException(502, str(e))
        return recipe_json(service, r, service.store.feedback())

    @app.get("/api/plan")
    def api_plan(woche: str = "aktuell"):
        return _plan_json(service.store.plan(week(woche)))

    @app.post("/api/sync")
    def api_sync():
        try:
            return {"ok": True, "ergebnis": service.sync_paprika()}
        except PaprikaError as e:
            raise HTTPException(502, str(e))

    @app.post("/api/plaene/{which}/erzeugen")
    def api_generate(which: str):
        sync_error = service.try_sync_paprika()
        body = _plan_json(service.generate(week(which), notify=True))
        if sync_error:
            body["sync_fehler"] = sync_error
        return body

    @app.get("/api/heute")
    def api_today():
        today = date.today()
        plan = service.store.plan(service.resolve_week("aktuell"))
        meal = next((m for m in plan.meals if m.day == today), None) if plan else None
        if meal is None:
            return {"gericht": "nichts geplant", "datum": today.isoformat()}
        return {"gericht": meal.name, "datum": today.isoformat(), "thema": meal.theme,
                "neu": meal.is_new, "freigegeben": plan.status == "freigegeben"}

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
        return {"ok": True}

    return app
