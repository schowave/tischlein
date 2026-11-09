"""FastAPI app: web UI, JSON API for Home Assistant, ICS feed."""

from __future__ import annotations

import io
import os
import zipfile
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from . import ics, paprika
from .config import WEEKDAY_NAMES, load_config
from .models import WeekPlan
from .service import Tischlein
from .store import Store

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


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
        service = Tischlein(
            Store(os.environ.get("TISCHLEIN_DB", "data/tischlein.db")),
            load_config(Path(os.environ.get("TISCHLEIN_CONFIG", "tischlein.toml"))),
            webhook_url=os.environ.get("HA_WEBHOOK_URL", ""),
        )
    app = FastAPI(title="Tischlein")
    app.state.service = service

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
    def index(request: Request):
        weeks = []
        for label, which in (("Diese Woche", "aktuell"), ("Nächste Woche", "naechste")):
            ws = service.resolve_week(which)
            weeks.append({"label": label, "start": ws, "plan": service.store.plan(ws)})
        recipes = service.store.recipes()
        return TEMPLATES.TemplateResponse(request, "index.html", {
            "weeks": weeks,
            "weekday_names": WEEKDAY_NAMES,
            "recipe_count": len(recipes),
            "feedback": service.store.feedback(),
            "config": service.config,
            "themes_without_recipes": [
                d.theme for d in service.config.days if not any(r.has_tag(d.theme) for r in recipes)
            ],
        })

    def back() -> RedirectResponse:
        return RedirectResponse("/", status_code=303)

    @app.post("/plaene/{which}/erzeugen")
    def ui_generate(which: str):
        service.generate(week(which))
        return back()

    @app.post("/plaene/{which}/tauschen")
    def ui_swap(which: str, tag: date = Form(...)):
        guard(service.swap, week(which), tag)
        return back()

    @app.post("/plaene/{which}/status")
    def ui_status(which: str, status: str = Form(...)):
        if status not in ("entwurf", "freigegeben"):
            raise HTTPException(400, "Status: entwurf oder freigegeben")
        guard(service.set_status, week(which), status)
        return back()

    @app.post("/rueckmeldung")
    def ui_feedback(rezept_uid: str = Form(...), urteil: str = Form(...)):
        if urteil not in ("ok", "nein"):
            raise HTTPException(400, "Urteil: ok oder nein")
        service.feedback(rezept_uid, urteil)  # type: ignore[arg-type]
        return back()

    @app.post("/import")
    async def ui_import(datei: UploadFile):
        try:
            recipes = paprika.read_export(io.BytesIO(await datei.read()))
        except (zipfile.BadZipFile, ValueError, KeyError):
            raise HTTPException(400, "Keine gültige .paprikarecipes-Datei")
        service.store.replace_recipes(recipes)
        return back()

    # API -------------------------------------------------------------------

    @app.get("/api/plan")
    def api_plan(woche: str = "aktuell"):
        return _plan_json(service.store.plan(week(woche)))

    @app.post("/api/plaene/{which}/erzeugen")
    def api_generate(which: str):
        return _plan_json(service.generate(week(which), notify=True))

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
        body = ics.render(service.store.approved_plans(), service.config)
        return Response(body, media_type="text/calendar; charset=utf-8")

    @app.get("/healthz")
    def health():
        return {"ok": True}

    return app
