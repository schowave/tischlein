"""Recipe ideas from an LLM: what the family likes, plus variety where the rotation is thin."""

from __future__ import annotations

from datetime import date

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

SYSTEM = (
    "Du schlägst einer Familie in Deutschland neue Mittagessen vor. Antworte nur mit JSON. "
    "Die Gerichte sollen eiweißreich, ausgewogen und kinderfreundlich sein, an einem Wochentag in "
    "höchstens 45 Minuten machbar, mit Zutaten aus einem normalen deutschen Supermarkt. "
    "Bring Abwechslung: andere Küchen, Zubereitungen und Gemüse als bisher, aber nah genug an dem, "
    "was die Kinder mögen. Schlage nichts vor, was es in der Sammlung schon gibt oder was abgelehnt wurde."
)


def focus_themes(supply: list[dict], themes: list[str]) -> list[str]:
    """Themes that lack kid-friendly recipes, or all themes when none does."""
    return [s["thema"] for s in supply if s["kinder_ok"] < s["bedarf"]] or list(themes)


def build_prompt(
    liked: list[str],
    disliked: list[str],
    existing: list[str],
    supply: list[dict],
    themes: list[str],
    count: int,
    today: date,
    theme: str | None = None,
    suggested: list[str] | None = None,
) -> str:
    """`theme` pins this request to one theme; `suggested` are ideas already made in this round."""
    focus = [theme] if theme else focus_themes(supply, themes)
    lines = [
        f"Monat: {MONTHS[today.month - 1]} (saisonal passend vorschlagen).",
        f"Mögliche Protein-Themen: {', '.join(themes)}.",
        f"Besonders gesucht, weil dort kinderfreundliche Gerichte fehlen: {', '.join(focus)}.",
        "Das mögen wir: " + ("; ".join(liked[:40]) or "noch keine Angaben"),
        "Das kam nicht gut an: " + ("; ".join(disliked[:20]) or "nichts"),
        "Schon in der Sammlung (nicht noch einmal vorschlagen): " + "; ".join(existing[:250]),
    ]
    if suggested:
        lines.append("Gerade schon vorgeschlagen (etwas deutlich anderes wählen): " + "; ".join(suggested))
    return "\n".join(
        [
            *lines,
            "",
            f"Schlage {count} {'Gericht' if count == 1 else 'Gerichte'} vor. Antwortformat:",
            '{"ideen": [{"name": "...", "thema": "<eines der Themen>", "warum": "<ein Satz, warum es passt>",'
            ' "zeit": "<z. B. 30 Min.>", "portionen": "4", "zutaten": ["400 g ...", "..."],'
            ' "zubereitung": ["Schritt 1 ...", "Schritt 2 ..."], "kinder_tipp": "<sichere Komponente>"}]}',
        ]
    )


def parse_ideas(answer, themes: list[str]) -> list[dict]:
    raw = answer.get("ideen", []) if isinstance(answer, dict) else answer
    out = []
    folded = {t.casefold(): t for t in themes}
    for i in raw or []:
        name = str(i.get("name", "")).strip()
        zutaten = [str(z).strip() for z in i.get("zutaten") or [] if str(z).strip()]
        if not name or not zutaten:
            continue
        out.append(
            {
                "name": name,
                "thema": folded.get(str(i.get("thema", "")).casefold(), ""),
                "warum": str(i.get("warum", "")).strip(),
                "zeit": str(i.get("zeit", "")).strip(),
                "portionen": str(i.get("portionen", "4")).strip() or "4",
                "zutaten": zutaten,
                "zubereitung": [str(s).strip() for s in i.get("zubereitung") or [] if str(s).strip()],
                "kinder_tipp": str(i.get("kinder_tipp", "")).strip(),
            }
        )
    return out


def paprika_recipe(idea: dict, uid: str, category_uids: list[str], now: str) -> dict:
    """A new Paprika recipe (all fields the sync API expects) from an accepted idea."""
    steps = "\n\n".join(idea.get("zubereitung") or [])
    description = idea.get("warum", "")
    if idea.get("kinder_tipp"):
        description += f"\nFür die Kinder: {idea['kinder_tipp']}"
    return {
        "uid": uid,
        "name": idea["name"],
        "ingredients": "\n".join(idea["zutaten"]),
        "directions": steps,
        "description": description.strip(),
        "notes": "Vorschlag von Tischlein",
        "nutritional_info": "",
        "servings": idea.get("portionen", "4"),
        "difficulty": "",
        "prep_time": "",
        "cook_time": "",
        "total_time": idea.get("zeit", ""),
        "source": "Tischlein",
        "source_url": "",
        "image_url": "",
        "photo": None,
        "photo_hash": None,
        "photo_large": None,
        "scale": None,
        "rating": 0,
        "categories": category_uids,
        "created": now,
        "in_trash": False,
        "is_pinned": False,
        "on_favorites": False,
        "on_grocery_list": False,
    }
