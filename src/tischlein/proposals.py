"""Vorschläge: the LLM proposes a theme for recipes without one and a cooking time where none is kept.

Pure: prompt and answer only. The family confirms each proposal; nothing is written by itself.
"""

from __future__ import annotations

BATCH = 12  # recipes per request: a long answer tends to come back cut off
MAX_MINUTES = 600

SYSTEM = (
    "Du ordnest Rezepte einer Familie in Deutschland ein. Antworte nur mit JSON. "
    "Für jedes Rezept nennst du das Protein-Thema, das am besten passt, und die gesamte Zeit vom "
    "Anfangen bis zum Essen in Minuten (Vorbereiten, Kochen, Backen, ohne Ruhezeit über Nacht). "
    "Passt keines der Themen, lässt du das Thema leer. Rate nicht: Maßgeblich sind die Zutaten."
)


def build_prompt(recipes: list[dict], themes: list[str]) -> str:
    """`recipes`: [{"nr", "name", "zutaten": [...], "schritte": int}]."""
    lines = [f"Mögliche Themen: {', '.join(themes)}.", "", "Rezepte:"]
    for r in recipes:
        lines.append(f"{r['nr']}. {r['name']} – Zutaten: {', '.join(r['zutaten'][:14]) or 'unbekannt'}")
    lines += ["", 'Antwortformat: {"rezepte": [{"nr": 1, "thema": "<Thema oder leer>", "minuten": 30}]}']
    return "\n".join(lines)


def parse_answer(answer, themes: list[str], count: int) -> dict[int, dict]:
    """nr -> {"thema": one of `themes` or "", "minuten": int or None}; anything else is dropped."""
    raw = answer.get("rezepte", []) if isinstance(answer, dict) else answer
    folded = {t.casefold(): t for t in themes}
    out: dict[int, dict] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            nr = int(item.get("nr"))
        except TypeError, ValueError:
            continue
        if not 1 <= nr <= count:
            continue
        try:
            minutes = int(item.get("minuten"))
        except TypeError, ValueError:
            minutes = None
        if minutes is not None and not 5 <= minutes <= MAX_MINUTES:
            minutes = None
        out[nr] = {"thema": folded.get(str(item.get("thema") or "").strip().casefold(), ""), "minuten": minutes}
    return out
