"""Live check of the Stöbern sources: does each site still answer the way the parsers expect?

    mise run quellen:check

Chefkoch, Eat Smarter and Cookidoo are read through undocumented interfaces (docs/stoebern-quellen.md).
The tests only know hand-made answers, so a site that changes goes unnoticed there. This asks each
source once for a common word and reads one Cookidoo recipe with its steps. Not part of
`mise run check`: it needs the network and fails when a site is down.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from tischlein import webrecipe
from tischlein.config import PlanConfig
from tischlein.service import Tischlein
from tischlein.store import Store

QUERY = "Lauch"
COOKIDOO_RECIPE = "https://cookidoo.de/recipes/recipe/de-DE/r285039"


def main() -> int:
    failed = False
    with tempfile.TemporaryDirectory() as tmp:
        svc = Tischlein(Store(Path(tmp) / "check.db"), PlanConfig())
        for source, site in webrecipe.SOURCES.items():
            try:
                hits = svc.search_web(QUERY, source)
                complete = [h for h in hits if h["name"] and h["url"].startswith("https://") and h["image"]]
                problem = "" if len(complete) >= 5 else f"nur {len(complete)} vollständige Treffer von {len(hits)}"
            except ValueError as e:
                problem = str(e.__cause__ or e)
            failed |= bool(problem)
            print(f"{'FEHLER' if problem else 'ok':6} Suche {site}: {problem or f'{len(hits)} Treffer'}")
        try:
            recipe = svc.web_recipe(COOKIDOO_RECIPE)
            problem = "" if recipe["steps"] and recipe["ingredients"] else "Rezept ohne Schritte oder Zutaten"
        except ValueError as e:
            problem = str(e)
        failed |= bool(problem)
        print(f"{'FEHLER' if problem else 'ok':6} Cookidoo-Rezept mit Schritten: {problem or len(recipe['steps'])}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
