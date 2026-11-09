"""Client for Paprika's unofficial cloud sync API.

Paprika does not document this API. Known from community clients:
- Login: POST /api/v1/account/login/ (form: email, password) -> {"result": {"token": ...}}.
  v2 login answers "Unrecognized client" for third parties, so we use v1.
- Reads: GET /api/v2/sync/{recipes,recipe/<uid>,categories}/ with "Authorization: Bearer <token>".
  (Verified 2026-11: v2 accepts only Bearer, v1 sync accepts only Basic auth.)
- Recipes reference categories by UID.
Responses are wrapped in {"result": ...}; we also accept bare JSON.
Paprika rate-limits aggressively per IP, so only changed recipes (by hash) are fetched.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .models import Recipe
from .paprika import recipe_from_json

BASE_URL = "https://www.paprikaapp.com/api"
DEFAULT_USER_AGENT = "Tischlein/0.1"

# transport(method, url, headers, body) -> (status, response bytes)
Transport = Callable[[str, str, Mapping[str, str], bytes | None], tuple[int, bytes]]


class PaprikaError(Exception):
    pass


def urllib_transport(method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except OSError as e:
        raise PaprikaError(f"Paprika nicht erreichbar: {e}") from e


def _decode(raw: bytes):
    try:
        raw = gzip.decompress(raw)
    except gzip.BadGzipFile:
        pass
    data = json.loads(raw)
    if isinstance(data, dict) and "error" in data:
        raise PaprikaError(f"Paprika meldet: {data['error']}")
    if isinstance(data, dict) and "result" in data:
        return data["result"]
    return data


@dataclass
class CachedRecipe:
    hash: str
    data: dict


@dataclass
class SyncResult:
    recipes: list[Recipe]
    cache: dict[str, CachedRecipe]
    fetched: int
    removed: int


class PaprikaClient:
    def __init__(
        self,
        email: str,
        password: str,
        transport: Transport = urllib_transport,
        user_agent: str = DEFAULT_USER_AGENT,
        base_url: str = BASE_URL,
        delay: float = 0.2,
    ):
        self._email = email
        self._password = password
        self._transport = transport
        self._user_agent = user_agent
        self._base = base_url.rstrip("/")
        self._delay = delay
        self._token: str | None = None

    def login(self) -> None:
        basic = b64encode(f"{self._email}:{self._password}".encode()).decode()
        body = urllib.parse.urlencode({"email": self._email, "password": self._password}).encode()
        status, raw = self._transport("POST", f"{self._base}/v1/account/login/", {
            "User-Agent": self._user_agent,
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }, body)
        if status in (401, 403):
            raise PaprikaError("Paprika-Anmeldung fehlgeschlagen: E-Mail oder Passwort falsch")
        if status != 200:
            raise PaprikaError(f"Paprika-Anmeldung fehlgeschlagen (HTTP {status})")
        result = _decode(raw)
        token = result.get("token") if isinstance(result, dict) else None
        if not token:
            raise PaprikaError("Paprika-Anmeldung lieferte kein Token")
        self._token = token

    def _check(self, path: str, status: int, raw: bytes):
        if status in (401, 403):
            raise PaprikaError("Paprika hat die Anfrage abgelehnt (Token ungültig?)")
        if status != 200:
            detail = raw[:200].decode(errors="replace")
            raise PaprikaError(f"Paprika-Anfrage {path} fehlgeschlagen (HTTP {status}): {detail}")
        return _decode(raw) if raw.strip() else None

    def _post(self, path: str, payload: object | None = None):
        """POST to the v2 sync API. Payloads go gzip-compressed in a multipart field `data`."""
        if self._token is None:
            self.login()
        headers = {"User-Agent": self._user_agent, "Authorization": f"Bearer {self._token}"}
        body = None
        if payload is not None:
            boundary = uuid.uuid4().hex
            body = (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="data"; filename="data"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode() + gzip.compress(json.dumps(payload).encode()) + f"\r\n--{boundary}--\r\n".encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        status, raw = self._transport("POST", f"{self._base}/v2/{path}", headers, body)
        return self._check(path, status, raw)

    def categories_raw(self) -> list[dict]:
        return list(self._get("sync/categories/"))

    def create_category(self, name: str, order_flag: int = 0) -> str:
        """Create a category. Undocumented: modelled on the recipe/meal endpoints."""
        uid = str(uuid.uuid4()).upper()
        self._post("sync/categories/", [
            {"uid": uid, "name": name, "parent_uid": None, "order_flag": order_flag, "deleted": False}
        ])
        return uid

    def update_recipe(self, data: dict) -> None:
        """Upload a full recipe (as returned by recipe()) with a fresh hash."""
        data = {k: v for k, v in data.items() if k != "photo_url"}
        for name in ("photo", "photo_hash", "photo_large"):
            data[name] = data.get(name) or None
        data.pop("hash", None)
        data["hash"] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest().upper()
        self._post(f"sync/recipe/{urllib.parse.quote(data['uid'])}/", data)

    def notify(self) -> None:
        """Tell the Paprika apps to pull changes."""
        self._post("sync/notify/")

    def _get(self, path: str):
        if self._token is None:
            self.login()
        status, raw = self._transport("GET", f"{self._base}/v2/{path}", {
            "User-Agent": self._user_agent,
            "Authorization": f"Bearer {self._token}",
        }, None)
        return self._check(path, status, raw)

    def categories(self) -> dict[str, str]:
        return {c["uid"]: c["name"] for c in self.categories_raw()
                if not c.get("deleted") and c.get("name")}

    def recipe_index(self) -> dict[str, str]:
        return {r["uid"]: r.get("hash", "") for r in self._get("sync/recipes/")}

    def recipe(self, uid: str) -> dict:
        return self._get(f"sync/recipe/{urllib.parse.quote(uid)}/")

    def sync(self, cache: Mapping[str, CachedRecipe]) -> SyncResult:
        """Fetch changed recipes. `cache` holds the raw recipes from the previous sync."""
        names = self.categories()
        index = self.recipe_index()
        new_cache: dict[str, CachedRecipe] = {}
        fetched = 0
        for uid, h in index.items():
            cached = cache.get(uid)
            if cached and h and cached.hash == h:
                new_cache[uid] = cached
                continue
            if fetched and self._delay:
                time.sleep(self._delay)
            new_cache[uid] = CachedRecipe(h, self.recipe(uid))
            fetched += 1
        recipes = [
            recipe_from_json(c.data, names)
            for c in new_cache.values()
            if not c.data.get("in_trash") and not c.data.get("deleted")
        ]
        removed = len(set(cache) - set(new_cache))
        return SyncResult(sorted(recipes, key=lambda r: r.name), new_cache, fetched, removed)
