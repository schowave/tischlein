from tischlein.shopping import collect, fallback_list, ingredient_lines, is_pantry, parse_llm

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
    lst = fallback_list(items)
    assert [(i["name"], i["recipes"]) for i in lst] == [("1 Zwiebel", "A, B"), ("200 g Reis", "A")]


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
