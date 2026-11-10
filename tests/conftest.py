import gzip
import io
import json
import zipfile

import pytest


def make_export(recipes: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for r in recipes:
            z.writestr(f"{r['name']}.paprikarecipe", gzip.compress(json.dumps(r).encode()))
    return buf.getvalue()


@pytest.fixture
def export_bytes() -> bytes:
    return make_export(
        [
            {
                "uid": "A1",
                "name": "Hähnchen-Bowl",
                "categories": ["Hähnchen", "Kinder-OK"],
                "rating": 5,
                "ingredients": "400 g Hähnchenbrust\n200 g Reis\nSalz",
                "photo_data": "AAAA",
            },
            {"uid": "B2", "name": "Fischstäbchen", "categories": ["Fisch", "Kinder-OK"], "rating": None},
            {"uid": "C3", "name": "Linsen-Dal", "categories": ["Hülsenfrüchte", "Neu"], "rating": 3},
        ]
    )
