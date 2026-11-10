from tischlein.shopping import (
    aisle,
    base_servings,
    collect,
    ingredient_lines,
    is_pantry,
    merge,
    parse_line,
    parse_llm,
    scale_line,
)

PANTRY = ["Salz", "Pfeffer", "Öl", "Olivenöl", "Zucker"]


def test_ingredient_lines_skip_headings_and_separators():
    text = "**Sauce**\n200 g Sahne\n=====\nTeig:\n2 **Eier**\n\n"
    assert ingredient_lines(text) == ["200 g Sahne", "2 Eier"]


def test_pantry_lines_are_skipped_but_real_ingredients_stay():
    assert is_pantry("Salz und Pfeffer", PANTRY)
    assert is_pantry("2 EL Olivenöl", PANTRY)
    assert is_pantry("n. B. Salz", PANTRY)
    assert not is_pantry("400 g Hähnchenbrust", PANTRY)
    assert not is_pantry("Salzkartoffeln", PANTRY)


def test_collect_and_fallback_merge_identical_lines():
    recipes = [
        {"name": "A", "ingredients": "1 Zwiebel\nSalz\n200 g Reis"},
        {"name": "B", "ingredients": "1 Zwiebel\n2 EL Öl"},
    ]
    items = collect(recipes, PANTRY)
    assert [i["line"] for i in items] == ["1 Zwiebel", "200 g Reis", "1 Zwiebel"]
    lst = merge(items)
    assert [(i["name"], i["amount"], i["recipes"]) for i in lst] == [("Zwiebel", "2", "A, B"), ("Reis", "200 g", "A")]


def test_parse_line_reads_paprika_formats():
    assert parse_line("1 Zwiebel(n), klein geschnitten") == {
        "qty": 1.0,
        "unit": "",
        "name": "Zwiebel",
        "plural": "n",
        "note": "klein geschnitten",
    }
    assert parse_line("0,50 TL Gemüsebrühepulver")["qty"] == 0.5
    assert (parse_line("½ Bund Petersilie")["qty"], parse_line("½ Bund Petersilie")["unit"]) == (0.5, "Bund")
    assert parse_line("1 ½ kg Kartoffeln")["qty"] == 1500 and parse_line("1 ½ kg Kartoffeln")["unit"] == "g"
    assert parse_line("200g Schmand")["qty"] == 200 and parse_line("1 Zehe/n Knoblauch")["name"] == "Knoblauch"
    assert parse_line("2-3 Karotten")["qty"] == 3 and parse_line("Muskat")["qty"] is None


def test_merge_adds_up_scales_and_sorts_by_aisle():
    items = [
        {"line": "1 Zwiebel(n)", "recipe": "A"},
        {"line": "2 Zwiebeln, gewürfelt", "recipe": "B", "factor": 1.5},
        {"line": "400 g Hähnchenbrust", "recipe": "A"},
        {"line": "600 g Hähnchenbrust", "recipe": "B"},
        {"line": "200 ml Sahne", "recipe": "B"},
        {"line": "5 Ei(er)", "recipe": "A"},
        {"line": "1 Ei", "recipe": "B"},
        {"line": "Muskat", "recipe": "A"},
    ]
    got = [(i["group"], i["name"], i["amount"], i["recipes"]) for i in merge(items)]
    assert got == [
        ("Obst & Gemüse", "Zwiebeln", "4", "A, B"),
        ("Fleisch & Fisch", "Hähnchenbrust", "1 kg", "A, B"),
        ("Kühlregal & Milch", "Eier", "6", "A, B"),
        ("Kühlregal & Milch", "Sahne", "200 ml", "B"),
        ("Gewürze & Saucen", "Muskat", "", "A"),
    ]


def test_helpers_for_portions_and_aisles():
    assert scale_line("400 g Reis", 1.5) == "600 g Reis" and scale_line("Muskat", 2) == "Muskat"
    assert base_servings("4 Portion/en") == 4 and base_servings("") is None and base_servings("0") is None
    assert aisle("TK Erbsen") == "Tiefkühl" and aisle("Paprikapulver") == "Gewürze & Saucen"
    assert aisle("passierte Tomaten") == "Trockenwaren & Konserven" and aisle("Weißwein") == "Sonstiges"


def test_parse_llm_normalises_groups_and_sorts():
    answer = {
        "items": [
            {"name": "Reis", "amount": "200 g", "group": "Trockenwaren & Konserven", "recipes": ["A"]},
            {"name": "Zwiebeln", "amount": "2", "group": "Obst & Gemüse", "recipes": ["A", "B"]},
            {"name": "Kaffee", "group": "Erfunden"},
            {"name": ""},
        ]
    }
    got = parse_llm(answer)
    assert [(i["name"], i["group"], i["recipes"]) for i in got] == [
        ("Zwiebeln", "Obst & Gemüse", "A, B"),
        ("Reis", "Trockenwaren & Konserven", "A"),
        ("Kaffee", "Sonstiges", ""),
    ]


def test_review_edge_cases():
    assert base_servings("1 Blech (12 Stück)") is None and base_servings("20 Stück") is None
    assert base_servings("2-3 Personen") == 2
    assert aisle("gehackte Petersilie") == "Obst & Gemüse" and aisle("Preiselbeeren") == "Obst & Gemüse"
    assert aisle("grüne Bohnen") == "Obst & Gemüse" and aisle("Räucherlachs") == "Fleisch & Fisch"
    assert parse_line("1½-2 EL Zucker")["qty"] == 2 and parse_line("1 ½ EL Zucker")["qty"] == 1.5
    assert merge([{"line": "2 g Safran", "recipe": "A", "factor": 0.25}])[0]["amount"] == "1 g"
    parmesan = merge([{"line": "200 g Parmesan", "recipe": "A"}, {"line": "Parmesan zum Bestreuen", "recipe": "B"}])
    assert parmesan[0]["amount"] == "200 g + etwas"
    kept = collect([{"name": "A", "ingredients": "(TK) 300 g Erbsen\n(à 400 g)"}], [])
    assert [i["line"] for i in kept] == ["(TK) 300 g Erbsen"]
