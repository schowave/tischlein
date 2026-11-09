import gzip
import json
from urllib.parse import parse_qs

import pytest

from tischlein.paprika_sync import CachedRecipe, PaprikaClient, PaprikaError

CATS = [
    {"uid": "c-fisch", "name": "Fisch"},
    {"uid": "c-kids", "name": "Kinder-OK"},
    {"uid": "c-old", "name": "Alt", "deleted": True},
]


class FakePaprika:
    def __init__(self, recipes: dict[str, dict], wrap=True, gzip_recipe=False, login_status=200):
        self.recipes = recipes
        self.wrap = wrap
        self.gzip_recipe = gzip_recipe
        self.login_status = login_status
        self.calls: list[tuple[str, str]] = []
        self.login_body = None

    def _ok(self, payload, compress=False):
        raw = json.dumps({"result": payload} if self.wrap else payload).encode()
        return 200, gzip.compress(raw) if compress else raw

    def __call__(self, method, url, headers, body):
        path = url.removeprefix("https://www.paprikaapp.com/api/")
        if path != "v1/account/login/":
            assert path.startswith("v2/"), path  # v1 sync rejects Bearer tokens
        path = path.removeprefix("v1/").removeprefix("v2/")
        self.calls.append((method, path))
        if path == "account/login/":
            self.login_body = parse_qs(body.decode())
            assert headers["Authorization"].startswith("Basic ")
            if self.login_status != 200:
                return self.login_status, b'{"error": "nope"}'
            return 200, json.dumps({"result": {"token": "T"}}).encode()
        assert headers["Authorization"] == "Bearer T"
        assert headers["User-Agent"]
        if path == "sync/categories/":
            return self._ok(CATS)
        if path == "sync/recipes/":
            return self._ok([{"uid": u, "hash": r["hash"]} for u, r in self.recipes.items()])
        if path.startswith("sync/recipe/"):
            uid = path.split("/")[2]
            return self._ok(self.recipes[uid], compress=self.gzip_recipe)
        return 404, b""


def recipe(uid, h, cats=(), **extra):
    return {"uid": uid, "hash": h, "name": f"Rezept {uid}", "categories": list(cats), "rating": 3, **extra}


def client(fake):
    return PaprikaClient("a@b.de", "geheim", transport=fake, delay=0)


def test_full_sync_maps_category_uids_and_skips_trash():
    fake = FakePaprika(
        {
            "R1": recipe("R1", "h1", ["c-fisch", "c-kids", "c-unknown", "c-old"]),
            "R2": recipe("R2", "h2", in_trash=True),
            "R3": recipe("R3", "h3", deleted=True),
        }
    )
    result = client(fake).sync({})

    assert fake.login_body == {"email": ["a@b.de"], "password": ["geheim"]}
    assert [(r.uid, r.tags, r.rating) for r in result.recipes] == [("R1", ("Fisch", "Kinder-OK"), 3)]
    assert result.fetched == 3
    assert set(result.cache) == {"R1", "R2", "R3"}


def test_incremental_sync_fetches_only_changed_and_counts_removed():
    fake = FakePaprika({"R1": recipe("R1", "h1"), "R2": recipe("R2", "h2-neu")})
    cache = {
        "R1": CachedRecipe("h1", recipe("R1", "h1")),
        "R2": CachedRecipe("h2", recipe("R2", "h2")),
        "R9": CachedRecipe("h9", recipe("R9", "h9")),
    }
    result = client(fake).sync(cache)

    fetched = [p for _, p in fake.calls if p.startswith("sync/recipe/")]
    assert fetched == ["sync/recipe/R2/"]
    assert result.fetched == 1
    assert result.removed == 1
    assert result.cache["R2"].hash == "h2-neu"
    assert [r.uid for r in result.recipes] == ["R1", "R2"]


def test_accepts_unwrapped_and_gzipped_responses():
    fake = FakePaprika({"R1": recipe("R1", "h1", ["c-fisch"])}, wrap=False, gzip_recipe=True)
    assert client(fake).sync({}).recipes[0].tags == ("Fisch",)


def test_login_failure_is_reported_without_password():
    fake = FakePaprika({}, login_status=401)
    with pytest.raises(PaprikaError, match="Anmeldung fehlgeschlagen") as exc:
        client(fake).sync({})
    assert "geheim" not in str(exc.value)
