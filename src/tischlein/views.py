"""View models for the web UI: everything the templates show, computed from the service."""

from __future__ import annotations

from datetime import date, timedelta

from .config import WEEKDAY_NAMES
from .models import Recipe, WeekPlan
from .service import Tischlein

MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]


def long_date(d: date) -> str:
    return f"{WEEKDAY_NAMES[d.weekday()]}, {d.day}. {MONTHS[d.month - 1]}"


def span(start: date, end: date) -> str:
    if start.month == end.month:
        return f"{start.day}.–{end.day}. {MONTHS[end.month - 1]}"
    return f"{start.day}. {MONTHS[start.month - 1]} – {end.day}. {MONTHS[end.month - 1]}"


def is_kid_ok(svc: Tischlein, r: Recipe | None, feedback: dict) -> bool:
    if r is None:
        return False
    return r.has_tag(svc.config.kid_tag) or feedback.get(r.uid) == "ok"


def recipe_json(svc: Tischlein, r: Recipe, feedback: dict) -> dict:
    themes = [t for t in svc.config.themes if r.has_tag(t)]
    return {
        "uid": r.uid, "name": r.name, "tags": list(r.tags), "rating": r.rating,
        "kinder_ok": is_kid_ok(svc, r, feedback), "kinder_tag": r.has_tag(svc.config.kid_tag),
        "themen": themes, "hauptspeise": r.has_tag(svc.config.main_tag),
        "rueckmeldung": feedback.get(r.uid),
        "bild": r.uid in svc_photos(svc),
    }


def svc_photos(svc: Tischlein) -> set[str]:
    """UIDs whose raw Paprika data has a photo (cached per request on the service)."""
    cache = getattr(svc, "_photo_uids", None)
    if cache is None or cache[0] != svc.store.meta("last_sync"):
        uids = {uid for uid, (_, raw) in svc.store.paprika_cache().items() if raw.get("photo_hash") or raw.get("photo")}
        cache = (svc.store.meta("last_sync"), uids)
        svc._photo_uids = cache
    return cache[1]


def week_view(svc: Tischlein, which: str | None, today: date) -> dict:
    cfg = svc.config
    feedback = svc.store.feedback()
    recipes = {r.uid: r for r in svc.store.recipes()}
    starts = {"aktuell": svc.resolve_week("aktuell", today), "naechste": svc.resolve_week("naechste", today)}
    plans: dict[str, WeekPlan | None] = {k: svc.store.plan(v) for k, v in starts.items()}
    if which not in starts:
        which = "aktuell" if plans["aktuell"] or not plans["naechste"] else "naechste"
    shown, start = plans[which], starts[which]

    by_day = {m.day: m for m in shown.meals} if shown else {}
    days = []
    for rule in cfg.days:
        d = start + timedelta(days=rule.weekday)
        meal = by_day.get(d)
        r = recipes.get(meal.recipe_uid) if meal and meal.recipe_uid else None
        days.append({
            "date": d, "weekday": WEEKDAY_NAMES[d.weekday()], "short": f"{d.day}.{d.month}.",
            "theme": rule.theme, "meal": meal, "is_today": d == today,
            "kinder_ok": is_kid_ok(svc, r, feedback),
            "needs_feedback": bool(meal and meal.is_new and meal.recipe_uid and meal.recipe_uid not in feedback),
            "feedback": feedback.get(meal.recipe_uid) if meal and meal.recipe_uid else None,
        })
    planned = sum(1 for x in days if x["meal"] and x["meal"].recipe_uid)
    kid_in_plan = sum(1 for x in days if x["meal"] and x["kinder_ok"])

    mains = [r for r in recipes.values() if r.has_tag(svc.config.main_tag)]
    kid_mains = sum(1 for r in mains if is_kid_ok(svc, r, feedback))

    today_meal = next((m for p in plans.values() if p for m in p.meals if m.day == today), None)
    headline = f"Mahlzeit. Heute gibt’s {today_meal.theme}." if today_meal else "Mahlzeit. Für heute ist nichts geplant."

    if not shown:
        finding = f"Für {span(start, start + timedelta(days=4))} gibt es noch keinen Plan."
    elif shown.status == "entwurf":
        finding = f"Der Plan für {span(start, start + timedelta(days=4))} wartet auf eure Freigabe."
    elif kid_in_plan == 0 and planned:
        finding = "Kein Gericht im Plan ist als Kinder-OK markiert."
    else:
        finding = f"Der Plan für {span(start, start + timedelta(days=4))} steht im Kalender."
    if shown and planned and kid_in_plan == 0 and shown.status == "entwurf":
        finding += " Keines der Gerichte ist bisher Kinder-OK."

    def week_tile(key: str) -> dict:
        p = plans[key]
        n = sum(1 for m in p.meals if m.recipe_uid) if p else 0
        state = "freigegeben" if p and p.status == "freigegeben" else ("Entwurf, wartet auf Freigabe" if p else "noch nicht geplant")
        return {"value": f"{n} von {len(cfg.days)}", "caption": state}

    supply = svc.theme_supply()
    lunch = lambda r: r.has_tag(cfg.main_tag) or any(r.has_tag(t) for t in cfg.themes)  # same rule as the planner
    trial = [r for r in recipes.values() if r.has_tag(cfg.new_tag) and lunch(r) and r.uid not in feedback]
    return {
        "which": which, "start": start, "plan": shown, "days": days,
        "range": span(start, start + timedelta(days=4)),
        "headline": headline, "today_line": long_date(today), "finding": finding,
        "tiles": [
            {"label": "Diese Woche", **week_tile("aktuell")},
            {"label": "Nächste Woche", **week_tile("naechste")},
            {"label": "Kinder-OK im Plan", "value": f"{kid_in_plan} von {planned}" if planned else "–",
             "caption": "Gerichte der gezeigten Woche" if planned else "kein Plan in dieser Woche"},
            {"label": "Kinder-OK-Rezepte", "value": str(kid_mains), "caption": f"von {len(mains)} Hauptspeisen"},
        ],
        "supply": supply,
        "trial": sorted(trial, key=lambda r: r.name.casefold())[:4], "trial_count": len(trial),
        "new_tag": cfg.new_tag, "kid_tag": cfg.kid_tag,
        "sync_enabled": svc.paprika is not None,
        "first_run": svc.paprika is None and not recipes,
        "last_sync": svc.store.meta("last_sync"), "sync_error": svc.store.meta("last_sync_error"),
        "recipe_count": len(recipes),
    }


def recipes_view(svc: Tischlein) -> dict:
    feedback = svc.store.feedback()
    recipes = sorted(svc.store.recipes(), key=lambda r: r.name.casefold())
    cfg = svc.config
    family = [cfg.kid_tag, cfg.new_tag]
    themes = list(cfg.themes)
    taken = {t.casefold() for t in family + themes}
    others = [c for c in svc.categories() if c.casefold() not in taken]
    items = [recipe_json(svc, r, feedback) for r in recipes]
    mains = [i for i in items if i["hauptspeise"]]
    return {
        "data": {
            "recipes": items,
            "groups": [
                {"name": "Für die Familie", "tags": family},
                {"name": "Protein-Thema", "tags": themes},
                {"name": "Weitere Kategorien", "tags": others},
            ],
            "kidTag": cfg.kid_tag, "mainTag": svc.config.main_tag, "themes": themes,
            "canWrite": svc.paprika is not None,
            "rotationNeed": cfg.cooldown_weeks + 1,
        },
        "count": len(items),
        "kid_count": sum(1 for i in mains if i["kinder_ok"]),
        "main_count": len(mains),
        "untagged": sum(1 for i in mains if not i["themen"]),
        "last_sync": svc.store.meta("last_sync"),
    }


def settings_view(svc: Tischlein, base_url: str) -> dict:
    cal = svc.calendar_settings()
    https_url = f"{base_url}/kalender/{cal['token']}.ics"
    webcal_url = "webcal://" + https_url.split("://", 1)[1]
    # preview: the next planned meal (approved or draft), else a sample from the schedule
    today = date.today()
    preview = None
    for which in ("aktuell", "naechste"):
        p = svc.store.plan(svc.resolve_week(which, today))
        if p:
            preview = next((m for m in p.meals if m.day >= today and m.recipe_uid), None)
        if preview:
            break
    hour, minute = (int(x) for x in cal["uhrzeit"].split(":"))
    start = hour * 60 + minute
    end = start + cal["minuten"]
    return {
        "account": svc.paprika_account(),
        "last_sync": svc.store.meta("last_sync"), "sync_error": svc.store.meta("last_sync_error"),
        "recipe_count": len(svc.store.recipes()),
        "cal": cal, "https_url": https_url, "webcal_url": webcal_url,
        "preview": {
            "day": long_date(preview.day) if preview else "Nächster freigegebener Tag",
            "time": f"{start // 60:02d}:{start % 60:02d}–{end // 60 % 24:02d}:{end % 60:02d}",
            "dish": preview.name if preview else "Gericht aus dem Plan",
            "theme": preview.theme if preview else "",
        },
    }
