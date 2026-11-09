"""Command line: tischlein import | plan | serve."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import WEEKDAY_NAMES
from .paprika import read_export
from .paprika_sync import PaprikaError
from .service import from_env


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tischlein", description="Familien-Essensplan")
    sub = parser.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="Paprika-Export (.paprikarecipes) einlesen")
    imp.add_argument("datei", type=Path)
    sub.add_parser("sync", help="Rezepte aus der Paprika-Cloud abgleichen (PAPRIKA_EMAIL/PAPRIKA_PASSWORD)")
    tags = sub.add_parser("tags-anwenden", help="Kategorie-Vorschläge aus der CSV nach Paprika schreiben")
    tags.add_argument("csv", type=Path)
    tags.add_argument("--ausfuehren", action="store_true", help="wirklich schreiben (sonst nur Probelauf)")
    tags.add_argument("--limit", type=int, help="nur die ersten N Rezepte")
    tags.add_argument("--nur", help="nur dieses Rezept (exakter Name)")
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

    svc = from_env()
    if args.cmd == "sync":
        try:
            print(svc.sync_paprika())
        except PaprikaError as e:
            sys.exit(f"Fehler: {e}")
    elif args.cmd == "tags-anwenden":
        from datetime import datetime

        from .tagging import apply_changes, read_changes

        if svc.paprika is None:
            sys.exit("Fehler: PAPRIKA_EMAIL/PAPRIKA_PASSWORD fehlen")
        changes = read_changes(args.csv)
        if args.nur:
            changes = [c for c in changes if c.name == args.nur]
        if args.limit:
            changes = changes[: args.limit]
        backup = Path(f"data/paprika-backup-{datetime.now():%Y%m%d-%H%M%S}.jsonl")
        print(("AUSFÜHREN" if args.ausfuehren else "PROBELAUF") + f": {len(changes)} Rezepte")
        try:
            r = apply_changes(svc.paprika, changes, execute=args.ausfuehren, backup=backup)
        except (PaprikaError, RuntimeError) as e:
            sys.exit(f"Fehler: {e}")
        print(f"\nKategorien neu: {len(r.created_categories)}, Rezepte geändert: {len(r.updated)}, "
              f"schon aktuell: {len(r.unchanged)}, fehlen: {len(r.missing)}")
        if args.ausfuehren and r.updated:
            print(f"Backup der vorherigen Fassungen: {backup}")
    elif args.cmd == "import":
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
