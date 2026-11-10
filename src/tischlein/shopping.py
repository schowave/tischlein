"""Shopping list from a week plan: collect ingredients, drop pantry staples, consolidate.

With an LLM the list is merged and sorted by aisle; without one it is a plain list per
recipe. Pure functions; the service does the I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

GROUPS = [
    "Obst & Gemüse",
    "Fleisch & Fisch",
    "Kühlregal & Milch",
    "Brot & Backwaren",
    "Trockenwaren & Konserven",
    "Gewürze & Saucen",
    "Tiefkühl",
    "Sonstiges",
]


def ingredient_lines(text: str) -> list[str]:
    """Ingredient lines of a Paprika recipe without section headings and separators."""
    out = []
    for raw in (text or "").splitlines():
        raw = raw.strip()
        if raw.startswith("**") and raw.rstrip(":").endswith("**"):
            continue  # **Sauce** is a section heading
        line = raw.replace("**", "")
        if not line or set(line) <= set("=-_* "):
            continue
        if line.endswith(":") and len(line) < 40:
            continue
        out.append(line)
    return out


def is_pantry(line: str, pantry: Iterable[str]) -> bool:
    """A line that only names a staple ("Salz und Pfeffer", "2 EL Öl") is skipped."""
    words = re.findall(r"[A-Za-zÄÖÜäöüß]+", line)
    staples = {p.casefold() for p in pantry}
    filler = {
        "und",
        "oder",
        "etwas",
        "el",
        "tl",
        "prise",
        "prisen",
        "n",
        "b",
        "nach",
        "geschmack",
        "bedarf",
        "zum",
        "braten",
        "für",
        "die",
        "form",
        "schwarzer",
        "weißer",
        "aus",
        "der",
        "mühle",
        "evtl",
        "gemahlen",
        "frisch",
        "edelsüß",
        "rosenscharf",
        "neutrales",
        "gestr",
        "gehäuft",
        "msp",
        "schuss",
    }
    meaningful = [w for w in words if w.casefold() not in filler]
    return bool(meaningful) and all(w.casefold() in staples for w in meaningful)


def collect(recipes: list[dict], pantry: Iterable[str]) -> list[dict]:
    """[{recipe, lines}] -> flat list of {name, recipes} without pantry staples."""
    items = []
    for r in recipes:
        for line in ingredient_lines(r.get("ingredients", "")):
            if not is_pantry(line, pantry):
                items.append({"line": line, "recipe": r["name"]})
    return items


def fallback_list(items: list[dict]) -> list[dict]:
    """Without an LLM: one entry per distinct line, grouped by recipe."""
    seen: dict[str, dict] = {}
    for i in items:
        key = i["line"].casefold()
        if key in seen:
            if i["recipe"] not in seen[key]["recipes"]:
                seen[key]["recipes"] += f", {i['recipe']}"
            continue
        seen[key] = {"name": i["line"], "amount": "", "group": i["recipe"], "recipes": i["recipe"]}
    return list(seen.values())


SYSTEM = (
    "Du erstellst Einkaufslisten für eine Familie in Deutschland. Antworte nur mit JSON. "
    "Fasse gleiche Zutaten aus mehreren Rezepten zusammen und addiere Mengen, wo die Einheiten passen. "
    "Schreibe Zutaten im Singular oder in der üblichen Einkaufsform (z. B. 'Zwiebeln', 'Hähnchenbrust'). "
    "Ordne jede Zutat genau einer dieser Gruppen zu: " + ", ".join(GROUPS) + ". "
    "Lass Grundvorräte weg, die schon herausgefiltert wurden, und erfinde nichts dazu."
)


def llm_prompt(items: list[dict]) -> str:
    lines = "\n".join(f"- {i['line']}  (für: {i['recipe']})" for i in items)
    return (
        "Zutaten der Woche:\n" + lines + "\n\n"
        'Antwortformat: {"items": [{"name": "...", "amount": "...", "group": "...", "recipes": ["..."]}]}'
    )


def parse_llm(answer) -> list[dict]:
    raw = answer.get("items", []) if isinstance(answer, dict) else answer
    out = []
    for i in raw or []:
        name = str(i.get("name", "")).strip()
        if not name:
            continue
        group = i.get("group") if i.get("group") in GROUPS else "Sonstiges"
        recipes = i.get("recipes") or []
        out.append(
            {
                "name": name,
                "amount": str(i.get("amount", "") or "").strip(),
                "group": group,
                "recipes": ", ".join(recipes) if isinstance(recipes, list) else str(recipes),
            }
        )
    out.sort(key=lambda i: (GROUPS.index(i["group"]), i["name"].casefold()))
    return out
