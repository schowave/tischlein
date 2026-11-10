"""Read Paprika exports (.paprikarecipes = ZIP of gzip-compressed JSON files)."""

from __future__ import annotations

import gzip
import json
import zipfile
from pathlib import Path
from typing import BinaryIO

from .models import Recipe


def recipe_from_json(data: dict, category_names: dict[str, str] | None = None) -> Recipe:
    """Build a Recipe. Export files carry category names; the sync API carries
    category UIDs, which `category_names` maps to names (unknown UIDs are dropped)."""
    rating = data.get("rating") or 0
    categories = [c for c in data.get("categories") or [] if c]
    if category_names is not None:
        categories = [category_names[c] for c in categories if c in category_names]
    return Recipe(
        uid=str(data["uid"]),
        name=(data.get("name") or "").strip(),
        tags=tuple(c.strip() for c in categories if c.strip()),
        rating=max(0, min(5, int(rating))),
        source_url=data.get("source_url") or "",
        total_time=data.get("total_time") or "",
    )


def read_export(source: Path | BinaryIO) -> list[Recipe]:
    return [recipe for recipe, _ in read_export_raw(source)]


def read_export_raw(source: Path | BinaryIO) -> list[tuple[Recipe, dict]]:
    """Recipes with their raw data (ingredients, directions ...); embedded photos are dropped."""
    out: list[tuple[Recipe, dict]] = []
    with zipfile.ZipFile(source) as archive:
        for entry in archive.infolist():
            if entry.is_dir() or not entry.filename.endswith(".paprikarecipe"):
                continue
            raw = archive.read(entry)
            try:
                raw = gzip.decompress(raw)
            except gzip.BadGzipFile:
                pass  # some exports store plain JSON
            data = json.loads(raw)
            data.pop("photo_data", None)
            out.append((recipe_from_json(data), data))
    return out
