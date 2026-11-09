import csv
import gzip
import hashlib
import json
import re

import pytest

from tischlein.paprika_sync import PaprikaClient
from tischlein.tagging import Change, apply_changes, read_changes


class WritablePaprika:
    """Fake Paprika that records writes and serves state back."""

    def __init__(self, ignore_category_create=False):
        self.cats = [{"uid": "C-HS", "name": "Hauptspeisen", "order_flag": 3, "parent_uid": None},
                     {"uid": "C-BEI", "name": "Beilage", "order_flag": 5, "parent_uid": None}]
        self.recipes = {
            "R1": {"uid": "R1", "name": "Bowl", "categories": ["C-HS", "C-USER"], "hash": "x",
                   "photo": "", "photo_hash": "", "photo_large": None, "photo_url": "http://p", "notes": "n"},
            "R2": {"uid": "R2", "name": "Püree", "categories": ["C-HS"], "hash": "y",
                   "photo": "a.jpg", "photo_hash": "h", "photo_large": None},
        }
        self.ignore_category_create = ignore_category_create
        self.posts: list[tuple[str, object]] = []

    @staticmethod
    def _payload(headers, body):
        if body is None:
            return None
        boundary = re.search(r"boundary=(\w+)", headers["Content-Type"]).group(1)
        part = body.split(f"--{boundary}".encode())[1]
        assert b'name="data"' in part
        raw = part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
        return json.loads(gzip.decompress(raw))

    def __call__(self, method, url, headers, body):
        path = url.removeprefix("https://www.paprikaapp.com/api/")
        if path == "v1/account/login/":
            return 200, json.dumps({"result": {"token": "T"}}).encode()
        assert headers["Authorization"] == "Bearer T" and path.startswith("v2/sync/")
        path = path.removeprefix("v2/sync/")
        ok = lambda x: (200, json.dumps({"result": x}).encode())
        if method == "GET":
            if path == "categories/":
                return ok(self.cats)
            if path == "recipes/":
                return ok([{"uid": u, "hash": r["hash"]} for u, r in self.recipes.items()])
            return ok(self.recipes[path.split("/")[1]])
        payload = self._payload(headers, body)
        self.posts.append((path, payload))
        if path == "categories/":
            if not self.ignore_category_create:
                self.cats += payload
            return ok(True)
        if path.startswith("recipe/"):
            self.recipes[payload["uid"]] = payload
            return ok(True)
        if path == "notify/":
            return ok(True)
        return 404, b""


def client(fake):
    return PaprikaClient("a@b.de", "pw", transport=fake, delay=0)


CHANGES = [
    Change("R1", "Bowl", add=("Hähnchen",), remove=()),
    Change("R2", "Püree", add=("Beilage",), remove=("Hauptspeisen",)),
]


def test_dry_run_writes_nothing():
    fake = WritablePaprika()
    r = apply_changes(client(fake), CHANGES, execute=False, log=lambda _: None)
    assert fake.posts == []
    assert r.created_categories == ["Hähnchen"] and r.updated == ["Bowl", "Püree"]


def test_execute_creates_category_applies_delta_and_notifies(tmp_path):
    fake = WritablePaprika()
    backup = tmp_path / "b.jsonl"
    r = apply_changes(client(fake), CHANGES, execute=True, backup=backup, log=lambda _: None, delay=0)

    new_cat = next(c for c in fake.cats if c["name"] == "Hähnchen")
    assert new_cat["uid"] == new_cat["uid"].upper() and new_cat["order_flag"] == 6
    bowl = fake.recipes["R1"]
    assert bowl["categories"] == ["C-HS", "C-USER", new_cat["uid"]]  # unknown user category kept
    assert bowl["notes"] == "n" and "photo_url" not in bowl
    assert bowl["photo"] is None and bowl["photo_hash"] is None
    expected = hashlib.sha256(json.dumps({k: v for k, v in bowl.items() if k != "hash"}, sort_keys=True).encode()).hexdigest().upper()
    assert bowl["hash"] == expected
    assert fake.recipes["R2"]["categories"] == ["C-BEI"]
    assert fake.recipes["R2"]["photo"] == "a.jpg"
    assert [p for p, _ in fake.posts][-1] == "notify/"
    assert [json.loads(l)["uid"] for l in backup.read_text().splitlines()] == ["R1", "R2"]

    # second run is a no-op
    fake.posts.clear()
    r2 = apply_changes(client(fake), CHANGES, execute=True, log=lambda _: None, delay=0)
    assert r2.unchanged == ["Bowl", "Püree"] and fake.posts == []


def test_stops_when_category_create_is_ignored():
    fake = WritablePaprika(ignore_category_create=True)
    with pytest.raises(RuntimeError, match="nicht angelegt"):
        apply_changes(client(fake), CHANGES, execute=True, log=lambda _: None)
    assert not any(p.startswith("recipe/") for p, _ in fake.posts)


def test_read_changes_only_takes_confirmed_rows(tmp_path):
    p = tmp_path / "v.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["uid", "Rezept", "Bisher", "Thema", "Hinzufügen", "Entfernen", "Neu", "Hinweis", "Übernehmen"])
        w.writerow(["R1", "Bowl", "Hauptspeisen", "Hähnchen, Pasta/Reis", "", "", "", "", "ja"])
        w.writerow(["R2", "Püree", "Hauptspeisen", "", "Beilage", "Hauptspeisen", "", "", ""])
    assert read_changes(p) == [Change("R1", "Bowl", ("Hähnchen", "Pasta/Reis"), ())]
