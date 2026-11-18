"""Web-Rezepte: read a recipe from a page (schema.org JSON-LD, else via the LLM) and build a Paprika
recipe from it. Pure functions: fetching the page happens in the service."""

from __future__ import annotations

import html as htmllib
import ipaddress
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_TEXT = 12000  # characters of page text for the LLM
TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|igshid|si)$", re.I)

# Words in the ingredients that point to a theme, for the themes of the default schedule. A theme
# not listed here is still found by its own name in the page's categories and keywords.
THEME_WORDS = {
    "Fisch": [
        "lachs",
        "kabeljau",
        "thunfisch",
        "seelachs",
        "forelle",
        "garnele",
        "fisch",
        "dorsch",
        "scholle",
        "pangasius",
        "zander",
        "makrele",
        "sardine",
        "shrimps",
        "krabben",
    ],
    "Hähnchen": ["hähnchen", "huhn", "hühn", "pute", "chicken", "geflügel"],
    "Hülsenfrüchte": ["linsen", "kichererbsen", "bohnen", "erbsen", "tofu", "edamame"],
    "Pasta/Reis": [
        "nudeln",
        "spaghetti",
        "penne",
        "pasta",
        "reis",
        "risotto",
        "lasagne",
        "gnocchi",
        "tagliatelle",
        "fusilli",
        "farfalle",
        "maccheroni",
        "bulgur",
        "couscous",
        "graupen",
    ],
    "Eier/Quark": ["ei", "eier", "quark", "frischkäse", "hüttenkäse", "skyr"],
    "Fleisch": ["rind", "schwein", "hackfleisch", "hack", "lamm", "kalb", "speck", "schinken", "wurst"],
}


# Pages -------------------------------------------------------------------------


def find_url(text: str) -> str:
    """The first http(s) link in shared text ("Schau mal: Tofu-Curry https://…")."""
    m = re.search(r"https?://[^\s<>\"']+", text or "")
    return m.group(0).rstrip(".,;)") if m else ""


def clean_url(url: str) -> str:
    """The link as it is kept as the recipe's source: no fragment, no tracking parameters."""
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k)])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def normalize_url(url: str) -> str:
    """For comparing sources: like clean_url, and also no "www.", case or trailing slash differences."""
    parts = urlsplit(clean_url(url))
    host = parts.netloc.lower().removeprefix("www.")
    return urlunsplit(
        (
            "https" if parts.scheme.lower() in ("http", "https") else parts.scheme.lower(),
            host,
            parts.path.rstrip("/") or "/",
            parts.query,
            "",
        )
    )


def check_url(url: str) -> str:
    """The URL if Tischlein may fetch it (http/https with a host), else ValueError with the reason."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Das ist kein Link auf eine Webseite (http oder https).")
    return url.strip()


def is_public(address: str) -> bool:
    """Only addresses on the internet: never localhost or the home network (Home Assistant, router …)."""
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast


def page_text(html: str, limit: int = MAX_TEXT) -> str:
    """Readable text of a page for the LLM: no scripts, styles or navigation markup."""
    html = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|form)\b.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|li|h\d|div|tr)>", "\n", html)
    text = htmllib.unescape(re.sub(r"<[^>]+>", " ", html))
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)[:limit]


def _title(html: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    return htmllib.unescape(m.group(1)).strip() if m else ""


def _meta(html: str, prop: str) -> str:
    m = re.search(rf"""(?is)<meta[^>]+(?:property|name)=["']{prop}["'][^>]*content=["']([^"']*)["']""", html)
    return htmllib.unescape(m.group(1)).strip() if m else ""


# schema.org Recipe ------------------------------------------------------------------


def _blocks(html: str) -> list:
    out = []
    for m in re.finditer(r"(?is)<script[^>]*application/ld\+json[^>]*>(.*?)</script>", html):
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out


def _nodes(blocks: list) -> list[dict]:
    nodes = []
    for b in blocks:
        if isinstance(b, dict):
            nodes.extend(n for n in b.get("@graph", [b]) if isinstance(n, dict))
    return nodes


def _is(node: dict, kind: str) -> bool:
    t = node.get("@type")
    return kind in (t if isinstance(t, list) else [t])


def _text(value) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("name") or value.get("text") or ""
    text = re.sub(r"(?i)<br\s*/?>", " ", str(value or ""))
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", "", text))).strip()


def _resolve(value, ids: dict):
    if isinstance(value, dict) and "@id" in value and len(value) == 1:
        return ids.get(value["@id"], value)
    return value


def _image(value, ids: dict) -> str:
    value = _resolve(value, ids)
    if isinstance(value, list):
        return next((u for u in (_image(v, ids) for v in value) if u), "")
    if isinstance(value, dict):
        return str(value.get("contentUrl") or value.get("url") or "")
    return str(value or "")


def _steps(value) -> list[str]:
    """recipeInstructions: text, a list of strings, HowToSteps or HowToSections (heading + steps)."""
    if isinstance(value, str):  # one text: paragraphs or lines are the steps
        parts = re.split(r"(?i)\n\s*\n|</p>|<br\s*/?>\s*<br\s*/?>", value)
        if len(parts) == 1:
            parts = value.splitlines()
        return [t for t in (_text(p) for p in parts) if t]
    out = []
    for item in value or []:
        if isinstance(item, str):
            out.append(_text(item))
        elif isinstance(item, dict) and _is(item, "HowToSection"):
            steps = _steps(item.get("itemListElement"))
            name = _text(item.get("name"))
            if name and len(value) > 1:
                out.append(f"{name}:")
            out.extend(steps)
        elif isinstance(item, dict):
            out.append(_text(item.get("text") or item.get("name")))
    return [s for s in out if s]


def duration(value) -> str:
    """ISO 8601 (PT1H30M) as Paprika shows it ("1 Std. 30 Min."); other text stays as it is."""
    text = _text(value)
    m = re.fullmatch(r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?", text)
    if not m or not (m.group(1) or m.group(2)):
        return "" if text.startswith("P") else text
    hours, mins = int(m.group(1) or 0), int(m.group(2) or 0)
    hours, mins = hours + mins // 60, mins % 60
    return " ".join(x for x in (f"{hours} Std." if hours else "", f"{mins} Min." if mins else "") if x)


def _servings(value) -> str:
    values = value if isinstance(value, list) else [value]
    texts = [_text(v) for v in values if _text(v)]
    return next((t for t in texts if not t.isdigit()), texts[0] if texts else "")


def _keywords(node: dict) -> list[str]:
    out = []
    for field in ("recipeCategory", "keywords", "recipeCuisine"):
        value = node.get(field)
        for v in value if isinstance(value, list) else [value]:
            out.extend(w.strip() for w in str(v or "").split(",") if w.strip())
    return list(dict.fromkeys(out))


def from_schema(html: str, url: str) -> dict | None:
    """The page's schema.org Recipe as a Web-Rezept, or None when it has none (or no ingredients)."""
    nodes = _nodes(_blocks(html))
    ids = {n["@id"]: n for n in nodes if isinstance(n.get("@id"), str)}
    node = next((n for n in nodes if _is(n, "Recipe")), None)
    if node is None:
        return None
    ingredients = [_text(i) for i in node.get("recipeIngredient") or node.get("ingredients") or []]
    ingredients = [i for i in ingredients if i]
    if not ingredients:
        return None
    name = _text(node.get("name")) or _title(html)
    author = _text(_resolve(node.get("author"), ids))
    if author and name.endswith(f" von {author}"):  # Chefkoch: "Tofu-Curry von bettina_uphus"
        name = name[: -len(f" von {author}")]
    return {
        "name": name,
        "description": "",  # pages fill it with teaser text ("Jetzt bewerten ► Portionsrechner")
        "ingredients": ingredients,
        "steps": _steps(node.get("recipeInstructions")),
        "servings": _servings(node.get("recipeYield")),
        "prep_time": duration(node.get("prepTime")),
        "cook_time": duration(node.get("cookTime")),
        "total_time": duration(node.get("totalTime")),
        "image": _image(node.get("image"), ids) or _meta(html, "og:image"),
        "keywords": _keywords(node),
        "source": _site(html, url),
        "source_url": clean_url(url),
        "via": "seite",
    }


def _site(html: str, url: str) -> str:
    name = _meta(html, "og:site_name")
    if name:
        return name
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    return host.split(".")[0].capitalize() if host else ""


# LLM fallback --------------------------------------------------------------------------

SYSTEM = (
    "Du liest Rezepte aus dem Text einer Webseite. Antworte nur mit JSON. Erfinde nichts: "
    'steht auf der Seite kein Rezept mit Zutaten, antworte {"rezept": null}.'
)


def llm_prompt(text: str) -> str:
    return (
        "Hier ist der Text einer Rezeptseite. Gib das Rezept als JSON zurück:\n"
        '{"rezept": {"name": "…", "portionen": "4 Portionen", "zeit": "30 Min.", '
        '"zutaten": ["200 g Linsen", "…"], "zubereitung": ["Schritt 1 …", "…"], "stichworte": ["…"]}}\n'
        "Zutaten so, wie sie auf der Seite stehen, mit Mengen; jeder Schritt als eigener Eintrag.\n\n"
        f"Seitentext:\n{text}"
    )


def from_llm(answer, html: str, url: str) -> dict | None:
    data = answer.get("rezept") if isinstance(answer, dict) else None
    if not isinstance(data, dict):
        return None
    ingredients = [str(i).strip() for i in data.get("zutaten") or [] if str(i).strip()]
    name = str(data.get("name") or "").strip() or _title(html)
    if not ingredients or not name:
        return None
    return {
        "name": name[:200],
        "description": "",
        "ingredients": ingredients,
        "steps": [str(s).strip() for s in data.get("zubereitung") or [] if str(s).strip()],
        "servings": str(data.get("portionen") or ""),
        "prep_time": "",
        "cook_time": "",
        "total_time": str(data.get("zeit") or ""),
        "image": _meta(html, "og:image"),
        "keywords": [str(k) for k in data.get("stichworte") or []],
        "source": _site(html, url),
        "source_url": clean_url(url),
        "via": "llm",
    }


# Theme and Paprika ----------------------------------------------------------------------


def _matches(token: str, word: str) -> bool:
    """ "lachs" finds "Lachsfilet" and "Räucherlachs"; short words ("ei") only themselves."""
    return token == word or (len(word) > 3 and (token.startswith(word) or token.endswith(word)))


def suggest_theme(recipe: dict, themes: list[str]) -> str | None:
    """A theme only when it is clear: first the page's own categories and keywords, else words in
    the ingredients (THEME_WORDS). A tie means no suggestion."""

    def parts(theme: str) -> list[str]:
        return [p.strip().casefold() for p in theme.split("/") if p.strip()]

    keywords = {k.casefold() for k in recipe.get("keywords", [])}
    named = [t for t in themes if any(p in keywords for p in parts(t))]
    if len(named) == 1:
        return named[0]
    pool = named or themes
    tokens = [t for line in recipe.get("ingredients", []) for t in re.findall(r"[a-zäöüß]+", line.casefold())]
    scores = {}
    for theme in pool:
        words = THEME_WORDS.get(theme, parts(theme))
        scores[theme] = sum(1 for tok in tokens if any(_matches(tok, w) for w in words))
    best = max(scores.values(), default=0)
    winners = [t for t, s in scores.items() if s == best]
    return winners[0] if best > 0 and len(winners) == 1 else None


def paprika_recipe(recipe: dict, uid: str, category_uids: list[str], now: str) -> dict:
    """A new Paprika recipe (all fields the sync API expects) from a Web-Rezept."""
    return {
        "uid": uid,
        "name": recipe["name"],
        "ingredients": "\n".join(recipe["ingredients"]),
        "directions": "\n\n".join(recipe.get("steps") or []),
        "description": recipe.get("description", ""),
        "notes": recipe.get("notes", ""),
        "nutritional_info": "",
        "servings": recipe.get("servings", ""),
        "difficulty": "",
        "prep_time": recipe.get("prep_time", ""),
        "cook_time": recipe.get("cook_time", ""),
        "total_time": recipe.get("total_time", ""),
        "source": recipe.get("source", ""),
        "source_url": recipe.get("source_url", ""),
        "image_url": recipe.get("image", ""),
        "photo": None,
        "photo_hash": None,
        "photo_large": None,
        "scale": None,
        "rating": 0,
        "categories": category_uids,
        "created": now,
        "in_trash": False,
        "is_pinned": False,
        "on_favorites": False,
        "on_grocery_list": False,
    }


# Stöbern: where the family looks for new dishes. None of the searches is a documented public
# interface: Chefkoch's is the one its apps use, Eat Smarter's is its search page, Cookidoo's is the
# index behind its search page. Each may change or go away, so callers cope with an empty answer.
SOURCES = {"chefkoch": "Chefkoch", "eatsmarter": "Eat Smarter", "cookidoo": "Cookidoo"}
SEARCH_URL = "https://api.chefkoch.de/v2/recipes"
EATSMARTER = "https://eatsmarter.de"


def search_url(query: str, source: str = "chefkoch", limit: int = 24) -> str:
    if source == "eatsmarter":  # lunch dishes only: the plain search also finds smoothies and bread
        return EATSMARTER + "/suche/rezepte?" + urlencode({"ft": query, "f[0]": "meals_property:Mittagessen"})
    return SEARCH_URL + "?" + urlencode({"query": query, "limit": limit, "minimumRating": 4})


def search_results(data) -> list[dict]:
    """Hits of the Chefkoch search as cards: name, link, picture, stars, votes, minutes. Paid (Plus)
    recipes and anything without a link are left out."""
    out = []
    for hit in data.get("results", []) if isinstance(data, dict) else []:
        r = hit.get("recipe") if isinstance(hit, dict) else None
        if not isinstance(r, dict) or r.get("isPlus"):
            continue
        url, name = str(r.get("siteUrl") or ""), str(r.get("title") or "").strip()
        if not name or not url.startswith("https://www.chefkoch.de/"):
            continue
        rating = r.get("rating") if isinstance(r.get("rating"), dict) else {}
        image = str(r.get("previewImageUrlTemplate") or "") if r.get("hasImage") else ""
        minutes = r.get("preparationTime")
        out.append(
            {
                "name": name,
                "subtitle": str(r.get("subtitle") or "").strip(),
                "url": url,
                "image": image.replace("<format>", "crop-360x240") if image.startswith("https://") else "",
                "stars": round(float(rating.get("rating") or 0), 1),
                "votes": int(rating.get("numVotes") or 0),
                "minutes": int(minutes) if isinstance(minutes, int | float) and minutes > 0 else None,
            }
        )
    return out


def _first(pattern: str, text: str) -> str:
    m = re.search(pattern, text, re.S)
    return htmllib.unescape(m.group(1)).strip() if m else ""


def eatsmarter_results(page: str, limit: int = 24) -> list[dict]:
    """Hits of the Eat Smarter search page as the same cards as `search_results`, plus kcal per
    portion. Recipes there are tested by the editors, so hits without votes stay in."""
    out = []
    for teaser in page.split('class="teaser-wrapper"')[1:]:
        path = _first(r'class="teaser-wrapper-link"\s+href="(/rezepte/[^"#?]+)"', teaser)
        name = " ".join(_first(r'class="teaser-wrapper-link"[^>]*>(.*?)</a>', teaser).split())
        if not path or not name:
            continue
        image = _first(r'srcset="(https://images\.eatsmarter\.de/[^" ]+/300x225/[^" ]+)', teaser)
        image = image or _first(r'<img[^>]+src="(https://images\.eatsmarter\.de/[^"]+)"', teaser)
        votes = _first(r'class="rating-count">\s*\((\d+)\)', teaser)
        stars = _first(r'class="rating">\s*([\d.]+)\s*<', teaser)
        out.append(
            {
                "name": name,
                "subtitle": _first(r'class="premium-badge">([^<]*)<', teaser),
                "url": EATSMARTER + path,
                "image": image,
                "stars": round(float(stars), 1) if stars and votes else 0.0,
                "votes": int(votes or 0),
                "minutes": int(_first(r'class="preparation">\s*(\d+)\s*Min', teaser) or 0) or None,
                "kcal": int(_first(r'class="calories">\s*(\d+)\s*kcal', teaser) or 0) or None,
            }
        )
        if len(out) == limit:
            break
    return out


COOKIDOO = "https://cookidoo.de"
COOKIDOO_SEARCH = COOKIDOO + "/search/de-DE"


def cookidoo_key(page: str) -> dict | None:
    """What Cookidoo's search page hands every visitor for its search index: app, key, index and
    until when the key is good (epoch seconds). None when the page no longer carries it."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    try:
        props = json.loads(m.group(1))["props"]["pageProps"] if m else {}
        key = {
            "app": str(props["algoliaAppId"]),
            "key": str(props["algoliaApiKeyData"]["apiKey"]),
            "index": str(props["algoliaIndices"]["recipes"]["relevance"]),
            "until": int(props["algoliaApiKeyData"].get("validUntil") or 0),
        }
    except KeyError, TypeError, ValueError:
        return None
    ok = re.fullmatch(r"[A-Za-z0-9]+", key["app"]) and re.fullmatch(r"[\w-]+", key["index"]) and key["key"]
    return key if ok else None


def cookidoo_search_url(key: dict, query: str, limit: int = 24) -> str:
    params = {
        "query": query,
        "hitsPerPage": limit,
        "numericFilters": "rating>=4",
        "x-algolia-application-id": key["app"],
        "x-algolia-api-key": key["key"],
    }
    return f"https://{key['app']}-dsn.algolia.net/1/indexes/{key['index']}?" + urlencode(params)


def cookidoo_results(data) -> list[dict]:
    """Hits of the Cookidoo search as the same cards as `search_results`; the time is the whole time."""
    out = []
    for hit in data.get("hits", []) if isinstance(data, dict) else []:
        if not isinstance(hit, dict):
            continue
        rid, name = str(hit.get("id") or ""), str(hit.get("title") or "").strip()
        if not name or not re.fullmatch(r"r\d+", rid):
            continue
        image = str(hit.get("image") or "")
        image = image.replace("{assethost}", "assets.tmecosys.com").replace("{transformation}", "t_web276x230")
        seconds = hit.get("totalTime")
        out.append(
            {
                "name": name,
                "subtitle": "",
                "url": f"{COOKIDOO}/recipes/recipe/de-DE/{rid}",
                "image": image if image.startswith("https://assets.tmecosys.com/") else "",
                "stars": round(float(hit.get("rating") or 0), 1),
                "votes": int(hit.get("numberOfRatings") or 0),
                "minutes": round(seconds / 60) if isinstance(seconds, int | float) and seconds > 0 else None,
                "whole_time": True,
            }
        )
    return out


def is_cookidoo_recipe(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    own = re.fullmatch(r"cookidoo\.(?:[a-z]{2}|international|co\.uk|com\.au)", host)  # not cookidoo.de.example.org
    return bool(own and re.fullmatch(r"/recipes/recipe/[\w-]+/r\d+/?", parts.path))


def cookidoo_details(data) -> dict:
    """Steps and tips of a Cookidoo recipe. Its page shows no steps, but the same address answers
    with the whole recipe when asked for JSON. Thermomix settings stay in the text as written."""

    def plain(value) -> str:
        return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", "", str(value or ""))).split())

    steps = []
    for group in data.get("recipeStepGroups") or [] if isinstance(data, dict) else []:
        if not isinstance(group, dict):
            continue
        title = plain(group.get("title"))
        for step in group.get("recipeSteps") or []:
            text = plain(step.get("formattedText")) if isinstance(step, dict) else ""
            if text:
                steps.append(f"{title}: {text}" if title else text)
                title = ""
    tips = data.get("additionalInformation") or [] if isinstance(data, dict) else []
    notes = [plain(t.get("content")) for t in tips if isinstance(t, dict)]
    return {"steps": steps, "notes": "\n\n".join(n for n in notes if n)}
