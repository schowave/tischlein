"""Command line: tischlein import | plan | serve."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .config import WEEKDAY_NAMES, load_config
from .paprika import read_export
from .service import Tischlein
from .store import Store


def _service() -> Tischlein:
    return Tischlein(
        Store(os.environ.get("TISCHLEIN_DB", "data/tischlein.db")),
        load_config(Path(os.environ.get("TISCHLEIN_CONFIG", "tischlein.toml"))),
        webhook_url=os.environ.get("HA_WEBHOOK_URL", ""),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tischlein", description="Familien-Essensplan")
    sub = parser.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="Paprika-Export (.paprikarecipes) einlesen")
    imp.add_argument("datei", type=Path)
    plan = sub.add_parser("plan", help="Plan für eine Woche erzeugen (Entwurf)")
    plan.add_argument("--woche", default="naechste", help="aktuell | naechste | JJJJ-MM-TT")
    plan.add_argument("--freigeben", action="store_true", help="Plan direkt freigeben")
    serve = sub.add_parser("serve", help="Web-Oberfläche und API starten")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port)
        return

    svc = _service()
    if args.cmd == "import":
        count = svc.store.replace_recipes(read_export(args.datei))
        print(f"{count} Rezepte importiert.")
    elif args.cmd == "plan":
        p = svc.generate(svc.resolve_week(args.woche))
        if args.freigeben:
            p = svc.set_status(p.week_start, "freigegeben")
        print(f"Woche ab {p.week_start:%d.%m.%Y} ({p.status}):")
        for m in p.meals:
            flag = " [NEU]" if m.is_new else ""
            hint = f"  – {m.reason}" if m.reason else ""
            print(f"  {WEEKDAY_NAMES[m.day.weekday()]:<10} {m.theme:<14} {m.name}{flag}{hint}")
