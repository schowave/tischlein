from datetime import date

from tischlein.ideas import build_prompt, paprika_recipe, parse_ideas

THEMES = ["Hähnchen", "Fisch"]


def test_prompt_focuses_on_thin_themes_and_lists_context():
    supply = [{"thema": "Hähnchen", "kinder_ok": 3, "bedarf": 3}, {"thema": "Fisch", "kinder_ok": 1, "bedarf": 3}]
    p = build_prompt(["Fischstäbchen"], ["Lachs-Lasagne"], ["Bolognese"], supply, THEMES, 4, date(2026, 11, 10))
    assert "November" in p and "fehlen: Fisch" in p
    assert "Fischstäbchen" in p and "Lachs-Lasagne" in p and "Bolognese" in p and "Schlage 4" in p


def test_parse_ideas_keeps_valid_entries_and_normalises_theme():
    answer = {
        "ideen": [
            {
                "name": "Fisch-Tacos",
                "thema": "fisch",
                "zutaten": ["400 g Seelachs", "8 Tortillas"],
                "zubereitung": ["Braten", "Füllen"],
                "warum": "Wie Fischstäbchen, aber neu",
            },
            {"name": "Ohne Zutaten", "zutaten": []},
            {"name": "", "zutaten": ["x"]},
        ]
    }
    got = parse_ideas(answer, THEMES)
    assert len(got) == 1 and got[0]["thema"] == "Fisch" and got[0]["portionen"] == "4"


def test_paprika_recipe_has_required_fields():
    idea = parse_ideas(
        {"ideen": [{"name": "X", "zutaten": ["1 a"], "zubereitung": ["s1", "s2"], "kinder_tipp": "Reis extra"}]}, THEMES
    )[0]
    r = paprika_recipe(idea, "UID", ["C1"], "2026-11-10 12:00:00")
    assert r["uid"] == "UID" and r["categories"] == ["C1"] and r["in_trash"] is False
    assert r["directions"] == "s1\n\ns2" and "Reis extra" in r["description"]
