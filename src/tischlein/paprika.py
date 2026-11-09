"""Read Paprika exports (.paprikarecipes = ZIP of gzip-compressed JSON files)."""

from __future__ import annotations

import gzip
import json
import zipfile
from pathlib import Path
from typing import BinaryIO

from .models import Recipe


def recipe_from_json(data: dict) -> Recipe:
    rating = data.get("rating") or 0
    return Recipe(
        uid=str(data["uid"]),
        name=(data.get("name") or "").strip(),
        tags=tuple(c.strip() for c in data.get("categories") or [] if c and c.strip()),
        rating=max(0, min(5, int(rating))),
        source_url=data.get("source_url") or "",
        total_time=data.get("total_time") or "",
    )


def read_export(source: Path | BinaryIO) -> list[Recipe]:
    recipes: list[Recipe] = []
    with zipfile.ZipFile(source) as archive:
        for entry in archive.infolist():
            if entry.is_dir() or not entry.filename.endswith(".paprikarecipe"):
                continue
            raw = archive.read(entry)
            try:
                raw = gzip.decompress(raw)
            except gzip.BadGzipFile:
                pass  # some exports store plain JSON
            recipes.append(recipe_from_json(json.loads(raw)))
    return recipes
