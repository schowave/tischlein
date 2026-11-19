# Stöber-Quellen

Keine der drei Quellen hat eine dokumentierte öffentliche Schnittstelle. Alles hier ist gegen die echten
Seiten geprüft (Stand 2026-11). Ob es noch stimmt, sagt `mise run quellen:check`; derselbe Check läuft
wöchentlich als Workflow „Quellen“, dort ohne Chefkoch: Chefkoch sperrt die Server von GitHub mit 403. Ob
Chefkoch noch stimmt, zeigt nur der lokale Lauf.

Jede Quelle liefert dieselbe Trefferkarte: `name`, `subtitle`, `url`, `image`, `stars`, `votes`, `minutes`,
dazu je nach Quelle `kcal` oder `whole_time`. Ein Treffer öffnet die Vorschau (`web_recipe`), die das
schema.org-Rezept der Seite liest. Abgerufen wird nur über `Tischlein.web_get` (öffentliche Adressen, 10 s,
Größengrenze); es schickt als `Accept` die erwarteten Inhaltstypen.

## Chefkoch

- Suche: `GET https://api.chefkoch.de/v2/recipes?query=…&limit=24&minimumRating=4` → JSON, Treffer unter
  `results[].recipe`. Das ist die Schnittstelle der Chefkoch-Apps.
- Plus-Rezepte (`isPlus`) und Treffer ohne `siteUrl` auf `www.chefkoch.de` fallen weg; darum kommen oft
  weniger als 24 Treffer.
- Bild: `previewImageUrlTemplate` mit `<format>` → `crop-360x240`.
- Code: `webrecipe.search_url`, `webrecipe.search_results`.

## Eat Smarter

- Suche: die normale Suchseite als HTML,
  `GET https://eatsmarter.de/suche/rezepte?ft=…&f[0]=meals_property:Mittagessen`. Ohne den Filter kommen
  auch Smoothies und Brot. Weitere Filter gehen als `f[1]=…`, z. B. `nutrition_concepts:Eiweißreich`.
- Treffer: je ein Block `class="teaser-wrapper"`. Gelesen werden `a.teaser-wrapper-link` (Pfad, Name),
  das Bild `…/300x225/…` aus `srcset`, `span.rating`, `.rating-count`, `p.preparation`, `p.calories` und
  `.premium-badge` (ein Etikett wie „Protein-Kick zum Mittag“, kein Bezahlinhalt).
- Keine Sterne-Schwelle: Die Rezepte sind redaktionell geprüft und haben oft kaum Stimmen.
- Die Seite hat auch ein `ItemList` als JSON-LD, aber nur mit Adressen, ohne Namen und Bilder.
- Code: `webrecipe.search_url(query, "eatsmarter")`, `webrecipe.eatsmarter_results`.

## Cookidoo

- Suche in zwei Schritten. Erstens `GET https://cookidoo.de/search/de-DE`: Im `__NEXT_DATA__` der Seite
  stehen `algoliaAppId`, `algoliaApiKeyData.apiKey` (befristet, `validUntil`) und
  `algoliaIndices.recipes.relevance`. Den Schlüssel bekommt jeder Besucher, auch ohne Konto.
- Zweitens `GET https://<app>-dsn.algolia.net/1/indexes/<index>?query=…&hitsPerPage=24&numericFilters=rating>=4`
  mit App und Schlüssel als `x-algolia-application-id` und `x-algolia-api-key` in der Adresse. Treffer
  unter `hits[]`: `id` (`r123456`), `title`, `rating`, `numberOfRatings`, `totalTime` (Sekunden), `image`.
- Bild: Platzhalter `{assethost}` → `assets.tmecosys.com`, `{transformation}` → `t_web276x230`.
- Der Schlüssel wird im Service behalten (`_cookidoo_key`) und neu geholt, wenn er abläuft oder der Index
  ihn ablehnt. Auf Hauptgerichte eingrenzen lässt sich die Suche mit diesem Schlüssel nicht.
- Rezept: Die Seite `https://cookidoo.de/recipes/recipe/de-DE/<id>` hat ein schema.org-Rezept ohne
  Schritte. Dieselbe Adresse mit `Accept: application/json` liefert das ganze Rezept, ohne Anmeldung:
  Schritte unter `recipeStepGroups[].recipeSteps[].formattedText` (HTML mit `<nobr>`), Tipps unter
  `additionalInformation[].content`. Die Tipps gehen als Notiz nach Paprika.
- Falls Cookidoo das JSON einmal nur noch angemeldet herausgibt: Die Anmeldung der Cookidoo-App
  (OAuth mit PKCE gegen `ciam.prod.cookidoo.vorwerk-digital.com`) beschreibt die inoffizielle Bibliothek
  `cookidoo-api` (github.com/miaucl/cookidoo-api). Sie ist hier nicht eingebaut und nicht geprüft.
- Code: `webrecipe.cookidoo_key`, `cookidoo_search_url`, `cookidoo_results`, `is_cookidoo_recipe`,
  `cookidoo_details`; `Tischlein._search_cookidoo`.

## Eine Quelle ergänzen

1. Schlüssel und Name in `webrecipe.SOURCES`; der Umschalter und `quellen:check` lesen von dort.
2. Adresse und Parser als reine Funktionen in `webrecipe.py`, Abruf in `Tischlein.search_web`.
3. Test mit einer von Hand gebauten Antwort in `tests/test_alltag.py`, dann `mise run quellen:check`.
4. Prüfen, ob die Vorschau eines Treffers Zutaten und Schritte liest; die Quelle hier beschreiben.
