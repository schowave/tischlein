"""Shopping list from a week plan: collect ingredients, drop pantry staples, consolidate.

Amounts are scaled to the portions planned per meal. With an LLM the list is merged and
sorted by aisle; without one (or when it fails) `merge` does the same by rules: it parses
quantity and unit, adds up the same ingredient across recipes and sorts it into an aisle.
Pure functions; the service does the I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

GROUPS = [
    "Obst & Gemüse",
    "Fleisch & Fisch",
    "Kühlregal & Milch",
    "Brot & Backwaren",
    "Trockenwaren & Konserven",
    "Gewürze & Saucen",
    "Tiefkühl",
    "Sonstiges",
]


def ingredient_entries(text: str) -> list[tuple[str, bool]]:
    """(line, is_heading) for a Paprika ingredient text, without separators and empty lines."""
    out = []
    for raw in (text or "").splitlines():
        raw = raw.strip()
        heading = raw.startswith("**") and raw.rstrip(":").endswith("**")  # **Sauce**
        line = raw.replace("**", "")
        if not line or set(line) <= set("=-_* "):
            continue
        out.append((line, heading or (line.endswith(":") and len(line) < 40)))
    return out


def ingredient_lines(text: str) -> list[str]:
    """Ingredient lines of a Paprika recipe without section headings and separators."""
    return [line for line, heading in ingredient_entries(text) if not heading]


def is_pantry(line: str, pantry: Iterable[str]) -> bool:
    """A line that only names a staple ("Salz und Pfeffer", "2 EL Öl") is skipped."""
    words = re.findall(r"[A-Za-zÄÖÜäöüß]+", line)
    staples = {p.casefold() for p in pantry}
    filler = {
        "und",
        "oder",
        "etwas",
        "el",
        "tl",
        "prise",
        "prisen",
        "n",
        "b",
        "nach",
        "geschmack",
        "bedarf",
        "zum",
        "braten",
        "für",
        "die",
        "form",
        "schwarzer",
        "weißer",
        "aus",
        "der",
        "mühle",
        "evtl",
        "gemahlen",
        "frisch",
        "edelsüß",
        "rosenscharf",
        "neutrales",
        "gestr",
        "gehäuft",
        "msp",
        "schuss",
    }
    filler |= {"g", "kg", "ml", "l", "cl", "kalt", "kaltes", "warm", "warmes", "lauwarm", "lauwarmes", "heiß", "heißes"}
    staples |= {"wasser", "leitungswasser"}  # never on a shopping list
    meaningful = [w for w in words if w.casefold() not in filler]
    return bool(meaningful) and all(w.casefold() in staples for w in meaningful)


def collect(recipes: list[dict], pantry: Iterable[str]) -> list[dict]:
    """[{name, ingredients, factor?}] -> flat list of {line, recipe, factor} without pantry staples.

    `factor` scales the recipe to the portions planned (2.0 = twice the recipe)."""
    items = []
    for r in recipes:
        for line in ingredient_lines(r.get("ingredients", "")):
            if not is_pantry(line, pantry) and not is_fragment(line):
                items.append({"line": line, "recipe": r["name"], "factor": r.get("factor", 1)})
    return items


_PREPOSITIONS = {"in", "ohne", "mit", "à", "je", "zum", "zur", "für", "und", "oder", "dazu", "halbiert", "geviertelt"}


def is_fragment(line: str) -> bool:
    """A continuation of the line before, as Paprika imports of Thermomix recipes have them:
    "(à 400 g)", "halbiert", "in dünnen Scheiben", "ohne Stiele"."""
    line = line.strip()
    if re.fullmatch(r"\(.*\)", line):  # "(à 400 g)", but not "(TK) 300 g Erbsen"
        return True
    words = line.split()
    return bool(words) and words[0].casefold() in _PREPOSITIONS and not any(c.isdigit() for c in line)


def base_servings(text) -> int | None:
    """Paprika's free-text servings ("4", "4 Portion/en", "2-3 Personen") as a number of people.

    None when unknown or not a head count ("1 Blech", "20 Stück", "1 Auflaufform"), so the recipe
    is taken as written for the planned portions."""
    t = str(text or "").strip().casefold()
    if re.search(r"stück|stk|blech|form|glas|gläser|kuchen|muffin|liter|\bl\b|\bg\b", t):
        return None
    m = re.match(r"(\d+)", t)
    n = int(m.group(1)) if m else 0
    return n if n > 0 else None


FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3}
# unit spelling -> (canonical unit, factor to it)
UNITS = {
    "g": ("g", 1),
    "gr": ("g", 1),
    "gramm": ("g", 1),
    "kg": ("g", 1000),
    "ml": ("ml", 1),
    "l": ("ml", 1000),
    "liter": ("ml", 1000),
    "cl": ("ml", 10),
    "el": ("EL", 1),
    "tl": ("TL", 1),
    "msp": ("Msp.", 1),
    "prise": ("Prise", 1),
    "prisen": ("Prise", 1),
    "pck": ("Pck.", 1),
    "päckchen": ("Pck.", 1),
    "packung": ("Pck.", 1),
    "packungen": ("Pck.", 1),
    "pkg": ("Pck.", 1),
    "dose": ("Dose", 1),
    "dosen": ("Dose", 1),
    "becher": ("Becher", 1),
    "bund": ("Bund", 1),
    "zehe": ("Zehe", 1),
    "zehen": ("Zehe", 1),
    "stück": ("Stück", 1),
    "stk": ("Stück", 1),
    "scheibe": ("Scheibe", 1),
    "scheiben": ("Scheibe", 1),
    "würfel": ("Würfel", 1),
    "glas": ("Glas", 1),
    "gläser": ("Glas", 1),
    "handvoll": ("Handvoll", 1),
    "tasse": ("Tasse", 1),
    "tassen": ("Tasse", 1),
    "zweig": ("Zweig", 1),
    "zweige": ("Zweig", 1),
    "stange": ("Stange", 1),
    "stangen": ("Stange", 1),
}
_QTY = re.compile(r"^\s*(\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?|[½¼¾⅓⅔])(?:\s*([½¼¾]))?(?:\s*[-–]\s*(\d+(?:[.,]\d+)?))?\s*")
_MARKER = re.compile(r"\((?:n|en|e|er|s)\)|/(?:n|en|e|er|s)\b", re.I)

# aisle by keyword; checked in this order, first hit wins
AISLES = [
    ("Tiefkühl", ["tk ", "tk-", "tiefgekühlt", "tiefkühl", "gefroren"]),
    (
        "Gewürze & Saucen",
        [
            "pulver",
            "würze",
            "kräuterling",
            "lorbeer",
            "nelke",
            "pfefferkörner",
            "wacholder",
            "wachholder",
            "piment",
            "kardamom",
            "gewürz",
            "kreuzkümmel",
            "zimt",
            "muskat",
            "oregano",
            "senf",
            "ketchup",
            "sojasauce",
            "sojasoße",
            "essig",
            "honig",
            "sauce",
            "soße",
            "pesto",
            "sambal",
            "brühe",
            "fond",
            "curry",
            "chiliflocken",
            "vanille",
            "mayonnaise",
        ],
    ),
    (
        "Trockenwaren & Konserven",
        [
            "dose",
            "passiert",
            "tomatenmark",
            "nudel",
            "spaghetti",
            "pasta",
            "penne",
            "fusilli",
            "lasagne",
            "spätzle",
            "reis",
            "mehl",
            "linsen",
            "bohnen",
            "kichererbsen",
            "haferflocken",
            "couscous",
            "bulgur",
            "graupen",
            "grieß",
            "polenta",
            "kokosmilch",
            "semmelbrösel",
            "paniermehl",
            "hirse",
            "quinoa",
            "sesam",
            "nüsse",
            "nuss",
            "mandel",
            "backpulver",
            "pudding",
            "rosinen",
            "stärke",
            "gnocchi",
            "mais",
            "oliven",
            "kapern",
            "basmati",
            "jasmin",
        ],
    ),
    (
        "Fleisch & Fisch",
        [
            "hähnchen",
            "huhn",
            "hühner",
            "pute",
            "rind",
            "schwein",
            "hack",
            "speck",
            "schinken",
            "wurst",
            "würstchen",
            "lachs",
            "fisch",
            "kabeljau",
            "seelachs",
            "thunfisch",
            "garnele",
            "salami",
            "bacon",
            "steak",
            "chorizo",
            "geflügel",
            "lamm",
            "kalb",
            "forelle",
            "filet",
            "tatar",
            "gulasch",
            "geschnetzeltes",
            "frikadelle",
        ],
    ),
    (
        "Kühlregal & Milch",
        [
            "milch",
            "sahne",
            "quark",
            "joghurt",
            "käse",
            "butter",
            "schmand",
            "crème",
            "creme",
            "mozzarella",
            "feta",
            "parmesan",
            "halloumi",
            "tofu",
            "skyr",
            "ricotta",
            "mascarpone",
            "gouda",
            "emmentaler",
        ],
    ),
    ("Brot & Backwaren", ["brot", "brötchen", "baguette", "toast", "tortilla", "wrap", "fladen", "teig"]),
    (
        "Obst & Gemüse",
        [
            "suppengrün",
            "zwiebel",
            "knoblauch",
            "karotte",
            "möhre",
            "kartoffel",
            "paprika",
            "tomate",
            "zucchini",
            "brokkoli",
            "blumenkohl",
            "spinat",
            "salat",
            "gurke",
            "lauch",
            "porree",
            "kürbis",
            "apfel",
            "äpfel",
            "zitrone",
            "limette",
            "banane",
            "champignon",
            "pilz",
            "petersilie",
            "schnittlauch",
            "basilikum",
            "koriander",
            "ingwer",
            "erbsen",
            "sellerie",
            "kohl",
            "fenchel",
            "aubergine",
            "avocado",
            "beere",
            "rucola",
            "chili",
            "mango",
            "birne",
            "dill",
            "minze",
            "thymian",
            "rosmarin",
            "kohlrabi",
            "rettich",
            "radieschen",
            "obst",
            "gemüse",
            "kräuter",
            "schalotte",
            "pastinake",
            "rote bete",
            "mangold",
        ],
    ),
]


def _number(text: str) -> float:
    text = text.strip().replace(",", ".")
    if text in FRACTIONS:
        return FRACTIONS[text]
    if "/" in text:
        a, b = (float(x) for x in text.split("/"))
        return a / b if b else a
    return float(text)


_DESCRIPTORS = re.compile(
    r"^(?:(?:sehr\s+)?(?:groß|große|großer|großes|klein|kleine|kleiner|kleines|kl\.|mittelgroß|mittelgroße|"
    r"mittelgroßer|mittelgroßes|damengroß|damengroße|damengroßes|ganz|ganze|ganzer|ganzes|frisch|frische|frischer|"
    r"frisches|reif|reife|reifer|etwas|evtl\.|eventuell|n\.\s*b\.|nach\s+belieben|gehäuft|gehäufte|gestr\.|"
    r"gestrichen|gestrichene|stück|stk\.?|einige|wenig|optional)\s+)+",
    re.I,
)
_TRAILING = re.compile(r"\s+(?:(?:bei|nach)\s+(?:bedarf|geschmack|belieben)|zum\s+\w+|zur\s+\w+|für\s+.*)$", re.I)


def clean_name(core: str) -> tuple[str, str]:
    """'große Zwiebel(n) (rot)' -> ('Zwiebel', 'n'): without size words, notes and plural markers."""
    marker = _MARKER.search(core)
    plural = re.sub(r"[()/]", "", marker.group(0)) if marker else ""
    name = _MARKER.sub("", core)
    name = re.sub(r"\([^)]*\)", "", name)  # (Kochbananen), (à 400 g)
    name = re.sub(r"\(.*$", "", name)  # an unclosed "(Sellerie" cut off by the comma
    name = _DESCRIPTORS.sub("", name.strip())
    name = _TRAILING.sub("", name)
    return re.sub(r"\s+", " ", name).strip(" .-"), plural


def parse_line(line: str) -> dict:
    """'1 Zwiebel(n), klein geschnitten' -> {qty: 1.0, unit: '', name: 'Zwiebel', plural: 'n', note: ...}."""
    rest, qty = line.strip(), None
    m = _QTY.match(rest)
    if m:
        if m.group(3):  # a range "1½-2" counts as its upper end
            qty = _number(m.group(3))
        else:
            qty = _number(m.group(1)) + (FRACTIONS[m.group(2)] if m.group(2) else 0)
        rest = rest[m.end() :]
    unit = ""
    um = re.match(r"([A-Za-zÄÖÜäöüß]+)\.?(?:\(n\)|/n)?(?:,)?\s+", rest)
    if um and um.group(1).casefold() in UNITS and qty is not None:
        unit, factor = UNITS[um.group(1).casefold()]
        qty *= factor
        rest = rest[um.end() :]
    if qty is None:  # "200g Schmand": amount and unit written together
        glued = re.match(r"^(\d+(?:[.,]\d+)?)(g|kg|ml|l)\b\s*", rest)
        if glued:
            unit, factor = UNITS[glued.group(2)]
            qty, rest = _number(glued.group(1)) * factor, rest[glued.end() :]
    core, _, note = rest.partition(",")
    name, plural = clean_name(core)
    m2 = re.match(r"^(messerspitze|msp\.?)\s+", name, re.I)
    if m2:
        unit, name = "Msp.", name[m2.end() :]
    m3 = re.match(r"^([A-Za-zÄÖÜäöüß]+)\.?\s+", name)
    if m3 and not unit and qty is not None and m3.group(1).casefold() in UNITS:  # "1 kl. Bund Frühlingszwiebeln"
        unit, factor = UNITS[m3.group(1).casefold()]
        qty, name = qty * factor, name[m3.end() :]
    return {"qty": qty, "unit": unit, "name": name or line.strip(), "plural": plural, "note": note.strip()}


def scale_line(line: str, factor: float) -> str:
    """The line with its amount multiplied, for the LLM (which adds up what it is given)."""
    if abs(factor - 1) < 1e-9:
        return line
    p = parse_line(line)
    if p["qty"] is None:
        return line
    amount = format_amount({p["unit"]: p["qty"] * factor})
    return f"{amount} {p['name']}{', ' + p['note'] if p['note'] else ''}".strip()


def _key(name: str) -> str:
    k = re.sub(r"[\s-]+", "", name.casefold())  # Chili Pulver / Chilipulver
    k = {"eier": "ei", "äpfel": "apfel"}.get(k, k)
    if len(k) > 5 and k.endswith("n") and not k.endswith("ien"):
        k = k[:-1]  # Zwiebeln/Zwiebel, Karotten/Karotte, Kartoffeln/Kartoffel
    return k


def item_key(name: str) -> str:
    """The same item in Tischlein and Bring: "Zwiebeln" = "Zwiebel", "Eier" = "Ei" (no amounts)."""
    return _key(clean_name(name)[0] or name)


def aisle(name: str, unit: str = "") -> str:
    if unit in ("Dose", "Glas"):
        return "Trockenwaren & Konserven"
    n = f" {name.casefold()} "
    if "grüne bohnen" in n or "frische bohnen" in n:
        return "Obst & Gemüse"
    words_in = re.findall(r"[a-zäöüß]+", n)

    def hit(kw: str) -> bool:
        if " " in kw or not kw.isalpha():  # "rote bete", "tk "
            return kw in n
        # German compounds: the keyword starts a word (Hähnchenbrust) or ends it (Räucherlachs, Basmatireis),
        # so "gehackte" is no "hack" and "Preiselbeeren" no "reis"
        return any(w.startswith(kw) or re.search(kw + r"(?:n|en|e|s|er|es)?$", w) for w in words_in)

    for group, words in AISLES:
        if any(hit(w) for w in words):
            return group
    if re.search(r"\bei(er)?\b", n):
        return "Kühlregal & Milch"
    return "Sonstiges"


def _fmt(x: float) -> str:
    if 0 < x < 0.2:
        return "¼" if x > 0.15 else "etwas"
    if abs(x - round(x)) < 0.05:
        return str(round(x))
    for v, sym in ((0.5, "½"), (0.25, "¼"), (0.75, "¾")):
        if abs(x - int(x) - v) < 0.02:
            return (str(int(x)) if int(x) else "") + sym
    return f"{x:.1f}".replace(".", ",")


def format_amount(by_unit: dict[str, float]) -> str:
    parts = []
    for unit, q in by_unit.items():
        if unit == "g" and q >= 1000:
            parts.append(f"{_fmt(q / 1000)} kg")
        elif unit == "ml" and q >= 1000:
            parts.append(f"{_fmt(q / 1000)} l")
        elif unit in ("g", "ml"):
            parts.append(f"{max(1, int(q + 0.5))} {unit}")  # a pinch of saffron stays 1 g, not 0 g
        else:
            plural = {
                "Zehe": "Zehen",
                "Dose": "Dosen",
                "Scheibe": "Scheiben",
                "Stange": "Stangen",
                "Tasse": "Tassen",
                "Prise": "Prisen",
                "Zweig": "Zweige",
            }
            parts.append(f"{_fmt(q)} {plural.get(unit, unit) if q > 1 else unit}".strip())
    return " + ".join(parts)


def merge(items: list[dict]) -> list[dict]:
    """Without an LLM: the same ingredient from all recipes in one line, amounts added, by aisle."""
    merged: dict[str, dict] = {}
    for i in items:
        p = parse_line(i["line"])
        e = merged.setdefault(
            _key(p["name"]),
            {"name": p["name"], "plural": p["plural"], "units": {}, "loose": False, "recipes": [], "unit": p["unit"]},
        )
        if p["qty"] is None:
            e["loose"] = True
        else:
            e["units"][p["unit"]] = e["units"].get(p["unit"], 0) + p["qty"] * i.get("factor", 1)
        if i["recipe"] not in e["recipes"]:
            e["recipes"].append(i["recipe"])
    out = []
    for e in merged.values():
        name = e["name"]
        if e["plural"] and e["units"].get("", 0) > 1 and not name.endswith(e["plural"]):
            name += e["plural"]
        out.append(
            {
                "name": name,
                "amount": format_amount(e["units"]) + (" + etwas" if e["units"] and e["loose"] else ""),
                "group": aisle(e["name"], e["unit"]),
                "recipes": ", ".join(e["recipes"]),
            }
        )
    out.sort(key=lambda i: (GROUPS.index(i["group"]), i["name"].casefold()))
    return out


SYSTEM = (
    "Du erstellst Einkaufslisten für eine Familie in Deutschland. Antworte nur mit JSON. "
    "Fasse gleiche Zutaten aus mehreren Rezepten zusammen und addiere Mengen, wo die Einheiten passen. "
    "Schreibe Zutaten im Singular oder in der üblichen Einkaufsform (z. B. 'Zwiebeln', 'Hähnchenbrust'). "
    "Ordne jede Zutat genau einer dieser Gruppen zu: " + ", ".join(GROUPS) + ". "
    "Lass Grundvorräte weg, die schon herausgefiltert wurden, und erfinde nichts dazu."
)


def llm_prompt(items: list[dict]) -> str:
    lines = "\n".join(f"- {scale_line(i['line'], i.get('factor', 1))}  (für: {i['recipe']})" for i in items)
    return (
        "Zutaten der Woche:\n" + lines + "\n\n"
        'Antwortformat: {"items": [{"name": "...", "amount": "...", "group": "...", "recipes": ["..."]}]}'
    )


def parse_llm(answer) -> list[dict]:
    raw = answer.get("items", []) if isinstance(answer, dict) else answer
    out = []
    for i in raw or []:
        name = str(i.get("name", "")).strip()
        if not name:
            continue
        group = i.get("group") if i.get("group") in GROUPS else "Sonstiges"
        recipes = i.get("recipes") or []
        out.append(
            {
                "name": name,
                "amount": str(i.get("amount", "") or "").strip(),
                "group": group,
                "recipes": ", ".join(recipes) if isinstance(recipes, list) else str(recipes),
            }
        )
    out.sort(key=lambda i: (GROUPS.index(i["group"]), i["name"].casefold()))
    return out
