"""Web-Rezepte: reading a page, the theme suggestion, and taking it over into Paprika."""

import json
import random

import pytest
from test_features import _with_llm
from test_tagging import WritablePaprika, client

from tischlein import webrecipe
from tischlein.config import PlanConfig
from tischlein.service import Tischlein, _check_public
from tischlein.store import Store

URL = "https://www.chefkoch.de/rezepte/4451281790338065/Tofu-Curry-mit-Brokkoli.html"
GRAPH = {
    "@context": "https://schema.org",
    "@graph": [
        {"@type": "Person", "@id": "#author", "name": "bettina_uphus"},
        {"@type": "ImageObject", "@id": "#img", "contentUrl": "https://img.example/tofu.jpg"},
        {
            "@type": "Recipe",
            "name": "Tofu-Curry mit Brokkoli von bettina_uphus",
            "description": "Gib jetzt die erste Bewertung ab! ► Portionsrechner",
            "author": {"@id": "#author"},
            "image": {"@id": "#img"},
            "prepTime": "PT10M",
            "totalTime": "PT1H25M",
            "recipeYield": ["2", "2 Portionen"],
            "keywords": "Gemüse, Hauptspeise, Hülsenfrüchte",
            "recipeCategory": "Vegetarisch",
            "recipeIngredient": ["200 g Tofu", "500 g Brokkoli", "400 ml Kokosmilch"],
            "recipeInstructions": [
                {
                    "@type": "HowToSection",
                    "name": "Zubereitung",
                    "itemListElement": [
                        {"@type": "HowToStep", "text": "Tofu würfeln."},
                        {"@type": "HowToStep", "text": "Alles <b>garen</b>."},
                    ],
                }
            ],
        },
    ],
}
PAGE = (
    '<html><head><meta property="og:site_name" content="Chefkoch">'
    f'<script type="application/ld+json">{json.dumps(GRAPH)}</script></head><body>…</body></html>'
)
BLOG = "<html><head><title>Omas Linsen</title></head><body><h1>Omas Linsen</h1><p>250 g Linsen …</p></body></html>"
THEMES = ["Hähnchen", "Hülsenfrüchte", "Fisch", "Eier/Quark", "Pasta/Reis"]


def test_reads_schema_org_recipe_with_graph_references_and_sections():
    r = webrecipe.from_schema(PAGE, URL + "?utm_source=app#kommentare")
    assert r["name"] == "Tofu-Curry mit Brokkoli"
    assert r["ingredients"] == ["200 g Tofu", "500 g Brokkoli", "400 ml Kokosmilch"]
    assert r["steps"] == ["Tofu würfeln.", "Alles garen."]
    assert (r["servings"], r["prep_time"], r["total_time"]) == ("2 Portionen", "10 Min.", "1 Std. 25 Min.")
    assert r["image"] == "https://img.example/tofu.jpg" and r["source"] == "Chefkoch"
    assert r["source_url"] == URL and r["description"] == ""
    assert webrecipe.from_schema(BLOG, URL) is None


def test_theme_from_page_keywords_else_ingredients_and_none_when_unclear():
    assert webrecipe.suggest_theme({"keywords": ["Hülsenfrüchte"], "ingredients": []}, THEMES) == "Hülsenfrüchte"
    assert webrecipe.suggest_theme({"ingredients": ["2 Lachsfilets", "Salz"]}, THEMES) == "Fisch"
    assert webrecipe.suggest_theme({"ingredients": ["200 g Hähnchenbrust", "200 g Reis"]}, THEMES) is None
    assert webrecipe.suggest_theme({"ingredients": ["1 Ei", "1 Eisbergsalat"]}, THEMES) == "Eier/Quark"


def test_links_from_shared_text_and_comparison_without_tracking():
    assert webrecipe.find_url(f"Schau mal: Tofu-Curry {URL}.") == URL
    assert webrecipe.normalize_url("http://Chefkoch.de/rezepte/1/x.html/?utm_medium=app&portionen=4#a") == (
        "https://chefkoch.de/rezepte/1/x.html?portionen=4"
    )
    with pytest.raises(ValueError):
        webrecipe.check_url("file:///etc/passwd")


def test_never_fetches_from_the_home_network():
    for address in ("127.0.0.1", "192.168.1.20", "10.0.0.5", "::1", "169.254.1.1"):
        assert not webrecipe.is_public(address)
    assert webrecipe.is_public("93.184.216.34")
    with pytest.raises(ValueError):
        _check_public("http://localhost:8123/api/states")


@pytest.fixture
def svc(tmp_path):
    fake = WritablePaprika()
    s = Tischlein(Store(tmp_path / "t.db"), PlanConfig(), rng=random.Random(0), paprika=client(fake))
    s.sync_paprika()
    s.fake = fake
    s.pages = {URL: (PAGE.encode(), "text/html"), "https://img.example/tofu.jpg": (b"\xff\xd8jpeg", "image/jpeg")}
    s.fetched = []

    def get(url, max_bytes, types=()):
        s.fetched.append(url)
        if url not in s.pages:
            raise ValueError("Die Seite antwortet mit Fehler 404.")
        return s.pages[url]

    s.web_get = get
    return s


def test_take_over_into_paprika_with_source_categories_and_photo(svc):
    preview = svc.web_recipe(URL)
    assert preview["theme"] == "Hülsenfrüchte"
    recipe = svc.accept_web_recipe(URL, ["Neu", "Hauptspeisen", "Hülsenfrüchte", "Erfunden"])
    created = svc.fake.recipes[recipe.uid]
    assert created["source_url"] == URL and created["source"] == "Chefkoch"
    assert created["ingredients"].startswith("200 g Tofu") and created["directions"] == "Tofu würfeln.\n\nAlles garen."
    assert set(recipe.tags) == {"Neu", "Hauptspeisen", "Hülsenfrüchte"}
    assert svc.fake.photos[recipe.uid] == b"\xff\xd8jpeg"
    assert created["photo"].endswith(".jpg") and created["photo_hash"]
    assert recipe.uid not in svc.web_photo_uids()  # Paprika has it, no local copy needed


def test_a_photo_paprika_does_not_take_stays_local(svc):
    svc.fake.refuse_photos = True
    recipe = svc.accept_web_recipe(URL, ["Neu"])
    assert recipe.uid in svc.fake.recipes and not svc.fake.photos
    assert svc.recipe_photo(recipe.uid).read_bytes() == b"\xff\xd8jpeg"


def test_png_photos_stay_local(svc):
    svc.pages["https://img.example/tofu.jpg"] = (b"\x89PNG", "image/png")
    recipe = svc.accept_web_recipe(URL, ["Neu"])
    assert not svc.fake.photos and recipe.uid in svc.web_photo_uids()


def test_a_source_already_in_paprika_is_not_taken_over_twice(svc):
    svc.accept_web_recipe(URL, ["Neu"])
    assert svc.find_source(URL + "?utm_campaign=x").name == "Tofu-Curry mit Brokkoli"
    with pytest.raises(ValueError, match="Schon in Paprika"):
        svc.accept_web_recipe("http://chefkoch.de/rezepte/4451281790338065/Tofu-Curry-mit-Brokkoli.html", ["Neu"])


def test_page_without_recipe_format_uses_the_llm_or_says_why_not(svc):
    blog = "https://blog.example/linsen"
    svc.pages[blog] = (BLOG.encode(), "text/html")
    with pytest.raises(ValueError, match="kein Rezept"):
        svc.web_recipe(blog)
    llm = _with_llm(svc, [{"rezept": {"name": "Omas Linsen", "zutaten": ["250 g Linsen"], "zubereitung": ["Kochen"]}}])
    r = svc.web_recipe(blog)
    assert r["via"] == "llm" and r["ingredients"] == ["250 g Linsen"] and r["theme"] == "Hülsenfrüchte"
    assert "250 g Linsen" in llm.calls[0]["messages"][1]["content"]


def test_preview_page_and_accept_route(svc):
    from fastapi.testclient import TestClient

    from tischlein.app import create_app

    c = TestClient(create_app(svc))
    page = c.get("/rezept-holen", params={"text": f"Schau mal {URL}"}).text
    assert "Tofu-Curry mit Brokkoli" in page and "Nach Paprika übernehmen" in page
    assert 'value="Hülsenfrüchte" checked' in page
    r = c.post("/rezept-holen", data={"url": URL, "kategorien": ["Neu", "Fisch"]}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/rezepte?rezept=")
    assert "Schon in Paprika" in c.get("/rezept-holen", params={"url": URL}).text
    assert "lässt sich nicht lesen" in c.get("/rezept-holen", params={"url": "https://nix.example/a"}).text
    assert "Rezept-Link einfügen" in c.get("/ideen").text


def test_cookidoo_steps_come_from_the_same_address_asked_for_json(svc):
    url = "https://cookidoo.de/recipes/recipe/de-DE/r285039"
    node = {"@type": "Recipe", "name": "Hähnchen mit Reis", "recipeIngredient": ["1 TL Salz"]}
    page = f'<html><script type="application/ld+json">{json.dumps(node)}</script></html>'
    details = {
        "recipeStepGroups": [
            {
                "title": "",
                "recipeSteps": [{"title": "1", "formattedText": "<NOBR>½ TL Salz</NOBR> in die Schale geben."}],
            },
            {"title": "Sauce", "recipeSteps": [{"formattedText": "<nobr>5 Sek./Stufe 5</nobr> verr&uuml;hren."}, {}]},
        ],
        "additionalInformation": [{"content": "Das Öl verhindert Schaum."}, {"content": ""}],
    }
    asked = []

    def web(address, size, types=()):
        asked.append((address, types))
        if types == ("application/json",):
            return json.dumps(details).encode(), "application/json"
        return page.encode(), "text/html"

    svc.web_get = web
    r = svc.web_recipe(url + "?utm_source=app")
    assert asked[1] == (url, ("application/json",))
    assert r["steps"] == ["½ TL Salz in die Schale geben.", "Sauce: 5 Sek./Stufe 5 verrühren."]
    assert r["notes"] == "Das Öl verhindert Schaum."
    assert webrecipe.paprika_recipe(r, "u1", [], "2026-11-18 12:00:00")["notes"] == "Das Öl verhindert Schaum."

    def no_json(address, size, types=()):
        if types == ("application/json",):
            raise ValueError("Die Seite antwortet mit Fehler 403.")
        return page.encode(), "text/html"

    svc.web_get, svc._web_cache = no_json, {}
    assert svc.web_recipe(url)["steps"] == []  # still a recipe, with its ingredients
    assert not webrecipe.is_cookidoo_recipe("https://cookidoo.de.example.org/recipes/recipe/de-DE/r1")
    assert not webrecipe.is_cookidoo_recipe("https://cookidoo.de/search/de-DE")


def test_instructions_as_one_text_become_steps():
    assert webrecipe._steps("Zwiebeln schneiden.\n\nAnbraten.\n\nServieren.") == [
        "Zwiebeln schneiden.",
        "Anbraten.",
        "Servieren.",
    ]
    assert webrecipe._steps("<p>Schritt <b>eins</b></p><p>Schritt zwei</p>") == ["Schritt eins", "Schritt zwei"]


def test_accepting_with_einplanen_puts_the_recipe_on_the_wish_list(svc):
    from fastapi.testclient import TestClient

    from tischlein.app import create_app

    c = TestClient(create_app(svc))
    assert "Nächste Woche einplanen" in c.get("/rezept-holen", params={"url": URL}).text
    r = c.post("/rezept-holen", data={"url": URL, "kategorien": ["Neu"], "einplanen": "1"}, follow_redirects=False)
    assert "hinweis=" in r.headers["location"]
    assert [x.name for x in svc.wishes()] == ["Tofu-Curry mit Brokkoli"]
