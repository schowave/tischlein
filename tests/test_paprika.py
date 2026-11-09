import gzip
import io
import json
import zipfile

from tischlein.paprika import read_export


def test_reads_recipes_with_categories_as_tags(export_bytes):
    recipes = {r.uid: r for r in read_export(io.BytesIO(export_bytes))}

    assert set(recipes) == {"A1", "B2", "C3"}
    assert recipes["A1"].name == "Hähnchen-Bowl"
    assert recipes["A1"].tags == ("Hähnchen", "Kinder-OK")
    assert recipes["A1"].rating == 5
    assert recipes["B2"].rating == 0  # missing rating
    assert recipes["C3"].has_tag("neu")  # case-insensitive


def test_accepts_uncompressed_entries_and_ignores_other_files():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.paprikarecipe", json.dumps({"uid": "X", "name": "Omelett", "categories": []}))
        z.writestr("readme.txt", "ignore me")
        z.writestr("y.paprikarecipe", gzip.compress(json.dumps({"uid": "Y", "name": " Quark "}).encode()))

    recipes = read_export(io.BytesIO(buf.getvalue()))

    assert [(r.uid, r.name, r.tags) for r in recipes] == [("X", "Omelett", ()), ("Y", "Quark", ())]
