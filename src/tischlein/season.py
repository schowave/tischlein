"""Saison: which vegetables and fruit are in season in Germany, and what that means for a recipe.

Pure rules, no I/O. The calendar counts field and storage season together (cabbage in winter).
What is around all year (potatoes, carrots, onions, tinned tomatoes) is no seasonal produce here;
frozen, tinned and dried ingredients are skipped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

IN_SEASON = 1.5  # chance factor for a recipe with produce that is in season
OUT_OF_SEASON = 0.3  # ... with a clearly seasonal produce outside its time (asparagus in November)


@dataclass(frozen=True)
class Produce:
    name: str
    months: frozenset[int]
    words: frozenset[str]  # whole words that name it
    endings: tuple[str, ...] = ()  # compounds ending so count too ("Hokkaidokürbis")
    strict: bool = False  # hardly to be had fresh outside its season


def _months(first: int, last: int) -> frozenset[int]:
    """Months from first to last, wrapping over the new year (10, 2 = October to February)."""
    span = range(first, last + 1) if first <= last else [*range(first, 13), *range(1, last + 1)]
    return frozenset(span)


def _p(name: str, first: int, last: int, words: str, endings: str = "", strict: bool = False) -> Produce:
    return Produce(name, _months(first, last), frozenset(words.split()), tuple(endings.split()), strict)


PRODUCE: tuple[Produce, ...] = (
    _p("Spargel", 4, 6, "spargel spargelspitzen spargelstangen", "spargel", strict=True),
    _p("Rhabarber", 4, 6, "rhabarber", strict=True),
    _p("Bärlauch", 3, 5, "bärlauch", strict=True),
    _p("Erdbeeren", 5, 7, "erdbeere erdbeeren", strict=True),
    _p("Kürbis", 9, 1, "kürbis kürbisse kürbisfleisch hokkaido butternut", "kürbis", strict=True),
    _p("Grünkohl", 11, 2, "grünkohl", strict=True),
    _p("Pfifferlinge", 6, 10, "pfifferling pfifferlinge", strict=True),
    _p("Steinpilze", 8, 10, "steinpilz steinpilze"),
    _p("Zucchini", 6, 10, "zucchini zucchinis zucchino"),
    _p("Aubergine", 7, 10, "aubergine auberginen"),
    _p("Blumenkohl", 5, 11, "blumenkohl blumenkohlröschen"),
    _p("Brokkoli", 6, 11, "brokkoli broccoli brokkoliröschen"),
    _p("Fenchel", 6, 11, "fenchel fenchelknolle fenchelknollen"),
    _p("Kohlrabi", 5, 10, "kohlrabi kohlrabis"),
    _p("Mangold", 6, 10, "mangold"),
    _p("Spinat", 4, 11, "spinat blattspinat babyspinat"),
    _p("Radieschen", 4, 10, "radieschen"),
    _p("Spitzkohl", 5, 11, "spitzkohl"),
    _p("Rosenkohl", 10, 2, "rosenkohl"),
    _p("Wirsing", 9, 3, "wirsing wirsingkohl"),
    _p("Weißkohl", 9, 3, "weißkohl weisskohl weißkraut"),
    _p("Rotkohl", 9, 3, "rotkohl blaukraut rotkraut"),
    _p("Chinakohl", 9, 2, "chinakohl"),
    _p("Lauch", 8, 3, "lauch porree lauchstange lauchstangen"),
    _p("Pastinaken", 10, 3, "pastinake pastinaken"),
    _p("Rote Bete", 8, 3, "bete beete"),
    _p("Sellerie", 8, 3, "sellerie", "sellerie"),
    _p("Steckrübe", 10, 2, "steckrübe steckrüben"),
    _p("Schwarzwurzeln", 10, 3, "schwarzwurzel schwarzwurzeln"),
    _p("Topinambur", 10, 3, "topinambur"),
    _p("Feldsalat", 10, 3, "feldsalat"),
    _p("Süßkartoffeln", 9, 3, "süßkartoffel süßkartoffeln"),
    _p("Zwetschgen", 7, 10, "zwetschge zwetschgen pflaume pflaumen"),
    _p("Kirschen", 6, 8, "kirsche kirschen"),
)

# frozen, tinned, dried, ground to powder: not bound to the season
_PRESERVED = re.compile(
    r"\b(tk|tiefkühl\w*|tiefgekühlt\w*|tiefgefroren\w*|gefroren\w*|gefr|dose|dosen|glas|getrocknet\w*|eingelegt\w*"
    r"|pulver)\b"
)


def _produce_in(line: str) -> list[Produce]:
    text = line.lower()  # not casefold: that would turn "ß" into "ss" and miss "Süßkartoffel"
    if _PRESERVED.search(text):
        return []
    tokens = set(re.findall(r"[a-zäöüß]+", text))
    return [p for p in PRODUCE if tokens & p.words or any(t.endswith(e) for t in tokens for e in p.endings)]


def seasonal(lines: Iterable[str], month: int) -> tuple[list[str], list[str]]:
    """(produce in season, clearly seasonal produce out of season) named in the ingredient lines."""
    found: dict[str, Produce] = {}
    for line in lines:
        for p in _produce_in(line):
            found[p.name] = p
    ins = [p.name for p in found.values() if month in p.months]
    outs = [p.name for p in found.values() if p.strict and month not in p.months]
    return ins, outs


def factor(lines: Iterable[str], month: int) -> float:
    """Chance factor for the planner: out of season weighs more than in season."""
    ins, outs = seasonal(lines, month)
    if outs:
        return OUT_OF_SEASON
    return IN_SEASON if ins else 1.0


def in_season(month: int) -> list[str]:
    """Everything in season in `month`, what just came into season first (for browsing)."""
    before = 12 if month == 1 else month - 1
    now = [p for p in PRODUCE if month in p.months]
    return [p.name for p in sorted(now, key=lambda p: (before in p.months, p.name))]
