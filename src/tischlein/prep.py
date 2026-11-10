"""Preparation hints from a recipe's ingredients and directions (rules, no LLM)."""

from __future__ import annotations

import re

_RULES: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"\b(TK|tiefgek[üu]hlt|gefroren|Tiefk[üu]hl)", re.I),
        "Tiefkühlware am Vorabend in den Kühlschrank legen",
    ),
    (re.compile(r"\bmarinier|\bMarinade\b", re.I), "Am Vorabend marinieren"),
    (re.compile(r"\bHefe\b", re.I), "Hefeteig braucht Zeit zum Gehen, rechtzeitig ansetzen"),
    (re.compile(r"\bvom Vortag\b|\baltbacken\b|\bKnödelbrot\b", re.I), "Altbackenes Brot bzw. Brötchen bereitlegen"),
]
_DRIED = re.compile(
    r"\b(getrocknet|trocken)\w*\b[^;]*\b(Kichererbsen|Bohnen)\b|\b(Kichererbsen|Bohnen)\b[^;]*\bgetrocknet", re.I
)


def prep_hints(ingredients: str, directions: str = "") -> list[str]:
    """Things to do the day before (or earlier) so lunch works out."""
    text = f"{ingredients}\n{directions}"
    hints: list[str] = []
    for pattern, hint in _RULES:
        if pattern.search(text) and hint not in hints:
            hints.append(hint)
    if _DRIED.search(ingredients):
        hints.append("Getrocknete Hülsenfrüchte über Nacht einweichen")
    return hints
