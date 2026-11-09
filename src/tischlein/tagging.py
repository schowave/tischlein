"""Apply reviewed category changes (tag-vorschlaege.csv) to Paprika.

Only the delta is applied (Thema + Hinzufügen added, Entfernen removed) on a freshly
fetched recipe, so edits made in Paprika in the meantime are kept.
"""

from __future__ import annotations

import csv
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .paprika_sync import PaprikaClient


@dataclass(frozen=True)
class Change:
    uid: str
    name: str
    add: tuple[str, ...]
    remove: tuple[str, ...]


@dataclass
class Report:
    created_categories: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _split(value: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in value.split(",") if v.strip())


def read_changes(path: Path) -> list[Change]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    return [
        Change(r["uid"], r["Rezept"], _split(r["Thema"]) + _split(r["Hinzufügen"]), _split(r["Entfernen"]))
        for r in rows
        if r.get("Übernehmen", "").strip().lower() == "ja"
    ]


def apply_changes(
    client: PaprikaClient,
    changes: list[Change],
    execute: bool,
    backup: Path | None = None,
    log: Callable[[str], None] = print,
    delay: float = 0.3,
) -> Report:
    report = Report()
    cats = [c for c in client.categories_raw() if not c.get("deleted")]
    by_name = {c["name"].casefold(): c["uid"] for c in cats}
    names = {c["uid"]: c["name"] for c in cats}
    next_order = max((c.get("order_flag") or 0 for c in cats), default=0) + 1

    for wanted in sorted({n for ch in changes for n in ch.add}, key=str.casefold):
        if wanted.casefold() in by_name:
            continue
        log(f"Kategorie anlegen: {wanted}")
        report.created_categories.append(wanted)
        if execute:
            client.create_category(wanted, next_order)
            next_order += 1
            fresh = {c["name"].casefold(): c["uid"] for c in client.categories_raw() if not c.get("deleted")}
            if wanted.casefold() not in fresh:
                raise RuntimeError(f"Kategorie „{wanted}“ wurde nicht angelegt (Paprika hat die Anfrage ignoriert)")
            by_name[wanted.casefold()] = fresh[wanted.casefold()]
            names[fresh[wanted.casefold()]] = wanted
        else:
            placeholder = f"NEU:{wanted}"
            by_name[wanted.casefold()] = placeholder
            names[placeholder] = f"{wanted} (neu)"

    index = client.recipe_index()
    out = backup.open("a", encoding="utf-8") if (backup and execute) else None
    try:
        for i, ch in enumerate(changes):
            if ch.uid not in index:
                report.missing.append(ch.name)
                log(f"fehlt in Paprika: {ch.name}")
                continue
            data = client.recipe(ch.uid)
            current = list(data.get("categories") or [])
            remove_uids = {by_name[n.casefold()] for n in ch.remove if n.casefold() in by_name}
            target = [u for u in current if u not in remove_uids]
            for n in ch.add:
                u = by_name.get(n.casefold())
                if u and u not in target:
                    target.append(u)
            if target == current:
                report.unchanged.append(ch.name)
                continue
            before = ", ".join(names.get(u, "?") for u in current)
            after = ", ".join(names.get(u, "?") for u in target)
            log(f"{ch.name}: [{before}] → [{after}]")
            report.updated.append(ch.name)
            if execute:
                if out:
                    out.write(json.dumps(data, ensure_ascii=False) + "\n")
                    out.flush()
                client.update_recipe({**data, "categories": target})
                if delay and i < len(changes) - 1:
                    time.sleep(delay)
    finally:
        if out:
            out.close()
    if execute and (report.updated or report.created_categories):
        client.notify()
    return report
