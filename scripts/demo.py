"""Demo data for screenshots and the README GIF: eight made-up recipes, a planned week, a list.

    uv run --frozen python scripts/demo.py data-demo [--fotos <ordner mit jpg>]
    TISCHLEIN_DB=data-demo/tischlein.db uv run --frozen tischlein serve --port 8091

No Paprika account and no LLM: nothing leaves the machine. Photos are optional; with --fotos the
first eight .jpg files (sorted by name) become the recipe photos. Never point this at real data.
"""

from __future__ import annotations

import argparse
import random
import shutil
from datetime import date
from pathlib import Path

from tischlein.config import load_config
from tischlein.paprika import recipe_from_json
from tischlein.planner import week_start_of
from tischlein.service import Tischlein
from tischlein.store import Store

# (name, categories, servings, ingredients); photos are matched in this order
RECIPES = [
    (
        "Hähnchen-Curry mit Reis",
        ["Hähnchen", "Hauptspeisen", "Kinder-OK"],
        "4",
        "600 g Hähnchenbrust\n2 Zwiebel(n)\n1 Zehe/n Knoblauch\n400 ml Kokosmilch\n2 EL Currypulver\n250 g Basmati",
    ),
    (
        "Rotes Linsen-Dal",
        ["Hülsenfrüchte", "Hauptspeisen", "Kinder-OK"],
        "4",
        "250 g rote Linsen\n1 Zwiebel(n)\n2 Zehe/n Knoblauch\n1 Dose stückige Tomaten\n"
        "200 ml Kokosmilch\n1 Stück Ingwer",
    ),
    (
        "Backfisch mit Kartoffelecken",
        ["Fisch", "Hauptspeisen", "Kinder-OK"],
        "2 Portion/en",
        "300 g Seelachsfilet\n500 g Kartoffeln\n1 Ei(er)\n50 g Paniermehl\n1 Zitrone(n)",
    ),
    (
        "Kaiserschmarrn mit Apfelmus",
        ["Eier/Quark", "Hauptspeisen", "Kinder-OK"],
        "4",
        "4 Ei(er)\n250 ml Milch\n150 g Mehl\n1 Glas Apfelmus\n30 g Butter",
    ),
    (
        "Spaghetti Bolognese",
        ["Pasta/Reis", "Fleisch", "Hauptspeisen", "Kinder-OK"],
        "4",
        "500 g Spaghetti\n400 g Rinderhack\n1 Zwiebel(n)\n2 Karotte(n)\n1 Dose passierte Tomaten\n50 g Parmesan",
    ),
    (
        "Kürbissuppe mit Ingwer",
        ["Hauptspeisen", "Neu"],
        "4",
        "1 Hokkaido-Kürbis\n1 Zwiebel(n)\n1 Stück Ingwer\n200 ml Sahne\n800 ml Gemüsebrühe",
    ),
    (
        "Garnelen-Risotto",
        ["Fisch", "Pasta/Reis", "Hauptspeisen"],
        "4",
        "300 g Risottoreis\n250 g Garnelen\n1 Zwiebel(n)\n100 ml Weißwein\n50 g Parmesan\n1 l Gemüsebrühe",
    ),
    (
        "Chili con Carne",
        ["Fleisch", "Hülsenfrüchte", "Hauptspeisen", "Kinder-OK"],
        "4",
        "500 g Rinderhack\n2 Zwiebel(n)\n1 Dose Kidneybohnen\n1 Dose Mais\n2 Dose stückige Tomaten\n1 Paprika",
    ),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ziel", type=Path, help="Ordner für die Demo-Datenbank, z. B. data-demo")
    ap.add_argument("--fotos", type=Path, help="Ordner mit frei lizenzierten Fotos (.jpg)")
    args = ap.parse_args()
    args.ziel.mkdir(parents=True, exist_ok=True)
    db = args.ziel / "tischlein.db"
    if db.exists():
        db.unlink()
    photos = sorted(args.fotos.glob("*.jpg"))[: len(RECIPES)] if args.fotos else []
    (args.ziel / "bilder").mkdir(exist_ok=True)

    raws = []
    for n, (name, cats, servings, ingredients) in enumerate(RECIPES, start=1):
        raw = {
            "uid": f"DEMO{n}",
            "name": name,
            "categories": cats,
            "servings": servings,
            "rating": 4,
            "ingredients": ingredients,
            "directions": "",
            "hash": f"demo{n}",
        }
        if n <= len(photos):
            raw["photo_hash"] = f"demofoto{n:08d}"
            shutil.copy(photos[n - 1], args.ziel / "bilder" / f"DEMO{n}-{raw['photo_hash'][:16]}.jpg")
        raws.append(raw)

    svc = Tischlein(Store(str(db)), load_config(Path("tischlein.toml")), rng=random.Random(4))
    svc.store.replace_recipes([recipe_from_json(r) for r in raws])
    svc.store.replace_paprika_cache({r["uid"]: (r["hash"], r) for r in raws})
    week = week_start_of(date.today())
    plan = svc.generate(week)
    svc.set_servings(plan.meals[2].day, 6)  # Wednesday for six: the list scales it
    svc.generate_shopping(week)
    svc.store.add_shopping_item(week, "Kaffee", "500 g")
    print(f"Demo in {args.ziel}: {len(raws)} Rezepte, {len(photos)} Fotos, Plan ab {week:%d.%m.}")


if __name__ == "__main__":
    main()
