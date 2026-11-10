"""View models for the web UI: everything the templates show, computed from the service."""

from __future__ import annotations

import re
from datetime import date, timedelta

from . import shopping
from .config import WEEKDAY_NAMES
from .models import Recipe, WeekPlan
from .planner import week_start_of
from .service import Tischlein

MONTHS = [
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
]


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
        "uid": r.uid,
        "name": r.name,
        "tags": list(r.tags),
        "rating": r.rating,
        "kinder_ok": is_kid_ok(svc, r, feedback),
        "kinder_tag": r.has_tag(svc.config.kid_tag),
        "themen": themes,
        "hauptspeise": r.has_tag(svc.config.main_tag),
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


def _hero(svc: Tischlein, plans: dict, today: date, recipes: dict, feedback: dict) -> dict:
    """The dish the family asks about first: today's, or the next one planned, plus what to prepare.

    Meals with a recipe count even on a skip day, as in the week grid and day_info; free days are
    meals marked skipped. Preparation is shown only for the day before the dish it belongs to.
    """
    upcoming = sorted(
        ((m, p) for p in plans.values() if p for m in p.meals if m.recipe_uid and not m.skipped and m.day >= today),
        key=lambda mp: mp[0].day,
    )
    if not upcoming:
        return {"meal": None, "headline": "Mahlzeit. Gerade ist nichts geplant.", "prep": [], "photo": False}
    meal, plan = upcoming[0]
    tomorrow = today + timedelta(days=1)
    if meal.day == today:
        headline = f"Heute gibt’s {meal.name}."
    elif meal.day == tomorrow:
        headline = f"Morgen gibt’s {meal.name}."
    elif (meal.day - today).days < 7:
        headline = f"{WEEKDAY_NAMES[meal.day.weekday()]} gibt’s {meal.name}."
    else:
        headline = f"Am {long_date(meal.day)} gibt’s {meal.name}."
    # preparation belongs to the evening before: tomorrow's dish, whether or not it is the hero
    prep, prep_for = [], None
    nxt = next((m for m, _ in upcoming if m.day == tomorrow), None)
    if nxt:
        prep = svc.day_info(tomorrow)["vorbereitung"]
        prep_for = nxt.name if nxt is not meal else None
    raw = svc.store.raw_recipe(meal.recipe_uid)
    return {
        "meal": meal,
        "headline": headline,
        "date": long_date(meal.day),
        "draft": plan.status != "freigegeben",
        "prep": prep,
        "tomorrow": prep_for,
        "photo": meal.recipe_uid in svc_photos(svc),
        "kid_ok": is_kid_ok(svc, recipes.get(meal.recipe_uid), feedback),
        "time": ((raw[1] if raw else {}).get("total_time") or "").strip(),
    }


def cook_view(svc: Tischlein, day: date) -> dict:
    """The dish of `day` for cooking: ingredients scaled to the planned portions, steps, photo."""
    plan = svc.store.plan(week_start_of(day))
    meal = next((m for m in plan.meals if m.day == day and m.recipe_uid and not m.skipped), None) if plan else None
    if meal is None:
        raise KeyError(f"Am {day:%d.%m.} ist kein Gericht geplant.")
    entry = svc.store.raw_recipe(meal.recipe_uid)
    raw = entry[1] if entry else {}
    p = svc.week_servings(week_start_of(day)).get(day) or {"base": svc.config.portions, "servings": svc.config.portions}
    factor = p["servings"] / p["base"]
    ingredients = [
        {"text": line if head else shopping.scale_line(line, factor), "head": head}
        for line, head in shopping.ingredient_entries(raw.get("ingredients") or "")
    ]
    steps = [s.strip() for s in re.split(r"\n\s*\n|\n(?=\d+[.)]\s)", raw.get("directions") or "") if s.strip()]
    if len(steps) == 1:
        steps = [s.strip() for s in steps[0].splitlines() if s.strip()]
    return {
        "meal": meal,
        "date": long_date(day),
        "servings": p["servings"],
        "base": p["base"],
        "scaled": abs(factor - 1) > 1e-9,
        "ingredients": ingredients,
        "steps": steps,
        "time": (raw.get("total_time") or "").strip(),
        "photo": meal.recipe_uid in svc_photos(svc),
        "prep": svc.day_info(day).get("vorbereitung", []),
        "back": "/?woche=naechste" if week_start_of(day) > week_start_of(date.today()) else "/",
    }


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
    portions = svc.week_servings(start)
    quick = svc.store.quickdays(start, start + timedelta(days=6))
    skip = svc.skip_map(start)
    ratings = {(r["uid"], r["day"]): r for r in svc.store.ratings()}
    days = []
    for rule in cfg.days:
        d = start + timedelta(days=rule.weekday)
        meal = by_day.get(d)
        r = recipes.get(meal.recipe_uid) if meal and meal.recipe_uid else None
        days.append(
            {
                "date": d,
                "weekday": WEEKDAY_NAMES[d.weekday()],
                "short": f"{d.day}.{d.month}.",
                "theme": rule.theme,
                "meal": meal,
                "is_today": d == today,
                "kinder_ok": is_kid_ok(svc, r, feedback),
                "needs_feedback": bool(meal and meal.is_new and meal.recipe_uid and meal.recipe_uid not in feedback),
                "feedback": feedback.get(meal.recipe_uid) if meal and meal.recipe_uid else None,
                "free": bool(meal and meal.skipped) or (meal is None and d in skip),
                "free_reason": (meal.reason if meal and meal.skipped else skip.get(d, "")),
                "is_holiday": skip.get(d, "").startswith("Feiertag"),
                "can_rate": bool(meal and meal.recipe_uid and not meal.skipped and d <= today),
                "rating": ratings.get((meal.recipe_uid, d)) if meal and meal.recipe_uid else None,
                "has_photo": bool(meal and meal.recipe_uid and meal.recipe_uid in svc_photos(svc)),
                "portions": portions.get(d),
                "quick": d in quick,
            }
        )
    planned = sum(1 for x in days if x["meal"] and x["meal"].recipe_uid)
    kid_in_plan = sum(1 for x in days if x["meal"] and x["kinder_ok"])

    hero = _hero(svc, plans, today, recipes, feedback)
    if shown and hero["meal"] in shown.meals:
        hero["draft"] = False  # the finding line below already says this plan waits for approval

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

    supply = svc.theme_supply()

    def lunch(r: Recipe) -> bool:  # same rule as the planner's new slot
        return r.has_tag(cfg.main_tag) or any(r.has_tag(t) for t in cfg.themes)

    trial = [r for r in recipes.values() if r.has_tag(cfg.new_tag) and lunch(r) and r.uid not in feedback]
    return {
        "which": which,
        "start": start,
        "plan": shown,
        "days": days,
        "range": span(start, start + timedelta(days=4)),
        "hero": hero,
        "today_line": long_date(today),
        "finding": finding,
        "supply": supply,
        "trial": sorted(trial, key=lambda r: r.name.casefold())[:4],
        "trial_count": len(trial),
        "new_tag": cfg.new_tag,
        "kid_tag": cfg.kid_tag,
        "sync_enabled": svc.paprika is not None,
        "first_run": svc.paprika is None and not recipes,
        "last_sync": svc.store.meta("last_sync"),
        "sync_error": svc.store.meta("last_sync_error"),
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
    index = svc.ingredient_index()
    for i in items:  # ingredient names for "what can I cook with ...?"; no amounts, no headings
        i["zutaten"] = index.get(i["uid"], [])
    mains = [i for i in items if i["hauptspeise"]]
    return {
        "data": {
            "recipes": items,
            "groups": [
                {"name": "Für die Familie", "tags": family},
                {"name": "Protein-Thema", "tags": themes},
                {"name": "Weitere Kategorien", "tags": others},
            ],
            "kidTag": cfg.kid_tag,
            "mainTag": svc.config.main_tag,
            "themes": themes,
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
        "llm": svc.llm_settings(),
        "people": svc.people(),
        "dislikes": ", ".join(svc.dislikes()),
        "bring_lists": svc.bring_lists(),
        "bring_list": svc.bring_list(),
        "ha": bool(svc.webhook_url),
        "avoided": len(svc.avoided_uids()),
        "sync_minutes": svc.config.sync_minutes,
        "auto_check": svc.store.meta("auto_check"),
        "last_sync": svc.store.meta("last_sync"),
        "sync_error": svc.store.meta("last_sync_error"),
        "recipe_count": len(svc.store.recipes()),
        "cal": cal,
        "https_url": https_url,
        "webcal_url": webcal_url,
        "preview": {
            "day": long_date(preview.day) if preview else "Nächster freigegebener Tag",
            "time": f"{start // 60:02d}:{start % 60:02d}–{end // 60 % 24:02d}:{end % 60:02d}",
            "dish": preview.name if preview else "Gericht aus dem Plan",
            "theme": preview.theme if preview else "",
        },
    }


FREE_REASONS = ["Urlaub", "Auswärts essen", "Kita / Schule", "Reste", "Kein Mittagessen"]


def shopping_view(svc: Tischlein, week_start: date, which: str) -> dict:
    items = svc.store.shopping(week_start)
    groups: dict[str, list[dict]] = {}
    for i in items:
        groups.setdefault("Eigene Einträge" if i["manual"] else i["group"], []).append(i)
    plan = svc.store.plan(week_start)
    photos = svc_photos(svc)
    dishes = (
        [
            {
                "name": m.name,
                "uid": m.recipe_uid,
                "day": WEEKDAY_NAMES[m.day.weekday()],
                "photo": m.recipe_uid in photos,
            }
            for m in sorted(plan.meals, key=lambda m: m.day)
            if m.recipe_uid and not m.skipped
        ]
        if plan
        else []
    )
    return {
        "which": which,
        "start": week_start,
        "range": span(week_start, week_start + timedelta(days=4)),
        "groups": [{"name": g, "items": lst} for g, lst in groups.items()],
        "count": len(items),
        "open": sum(1 for i in items if not i["done"]),
        "generated": sum(1 for i in items if not i["manual"]),
        "bring": bool(svc.webhook_url),
        "bring_name": next((x["name"] for x in svc.bring_lists() if x["entity_id"] == svc.bring_list()), ""),
        "dishes": dishes,
        "has_plan": bool(dishes),
        "llm": svc.llm() is not None,
    }


def ideas_view(svc: Tischlein) -> dict:
    return {
        "ideas": svc.store.ideas("neu"),
        "accepted": svc.store.ideas("uebernommen")[:6],
        "llm": svc.llm() is not None,
        "can_write": svc.paprika is not None,
        "supply": svc.theme_supply(),
    }


def review_view(svc: Tischlein, today: date, weeks: int) -> dict:
    """What was cooked in the last `weeks` approved weeks, and what to cook again."""
    since = today - timedelta(weeks=weeks)
    recipes = {r.uid: r for r in svc.store.recipes()}
    feedback = svc.store.feedback()
    plans = [p for p in svc.store.approved_plans() if since <= p.week_start <= today]
    meals = [m for p in plans for m in p.meals if m.recipe_uid and not m.skipped and m.day <= today]
    ratings = [r for r in svc.store.ratings() if r["day"] >= since]
    verdict_of = {(r["uid"], r["day"]): r["verdict"] for r in ratings}

    themes: dict[str, int] = {}
    for m in meals:
        themes[m.theme] = themes.get(m.theme, 0) + 1
    top = max(themes.values(), default=0)

    counts = {"gut": 0, "mittel": 0, "schlecht": 0}
    for r in ratings:
        counts[r["verdict"]] += 1

    last = svc.store.last_cooked(today + timedelta(days=1))
    loved = {r["uid"] for r in svc.store.ratings() if r["verdict"] == "gut"}
    favourites = [
        recipes[u]
        for u in recipes
        if (u in loved or is_kid_ok(svc, recipes[u], feedback))
        and recipes[u].has_tag(svc.config.main_tag)
        and feedback.get(u) != "nein"
        and (u not in last or (today - last[u]).days > 28)
    ]
    favourites.sort(key=lambda r: (last.get(r.uid, date.min), r.name.casefold()))

    weeks_out = []
    for p in sorted(plans, key=lambda p: p.week_start, reverse=True):
        weeks_out.append(
            {
                "range": span(p.week_start, p.week_start + timedelta(days=4)),
                "meals": [
                    {
                        "day": WEEKDAY_NAMES[m.day.weekday()][:2],
                        "name": m.name,
                        "theme": m.theme,
                        "skipped": m.skipped,
                        "reason": m.reason,
                        "is_new": m.is_new,
                        "verdict": verdict_of.get((m.recipe_uid, m.day)),
                    }
                    for m in p.meals
                ],
            }
        )
    return {
        "weeks": weeks,
        "photos": svc_photos(svc),
        "cooked": len(meals),
        "distinct": len({m.recipe_uid for m in meals}),
        "kid_share": round(
            100 * sum(1 for m in meals if is_kid_ok(svc, recipes.get(m.recipe_uid), feedback)) / len(meals)
        )
        if meals
        else 0,
        "new_tried": [m for m in meals if m.is_new],
        "themes": [
            {"name": t, "count": c, "pct": round(100 * c / top) if top else 0}
            for t, c in sorted(themes.items(), key=lambda x: -x[1])
        ],
        "ratings": counts,
        "favourites": favourites[:6],
        "favourites_more": max(0, len(favourites) - 6),
        "last": last,
        "week_list": weeks_out,
        "today": today,
    }
