# Tischlein

Familien-Essensplan für Mittagessen. Tischlein rotiert bewährte, eiweißreiche Rezepte aus
Paprika nach einem Wochenschema (Thementage), plant pro Woche einen Neu-Slot und liefert den
Plan als Kalender-Abo (ICS), als JSON für Home Assistant und als kleine Web-Oberfläche.

Prinzip „Sous-Chef“: Tischlein schlägt vor, die Familie gibt frei. Nur freigegebene Pläne
landen im Kalender.

## Wo was steht

- Abläufe (setup, check, serve, Docker, Migrationen): `mise tasks`. Werkzeuge und Versionen
  verwaltet mise; immer über mise ausführen. Konfiguration und Umgebungsvariablen: README.
- Bevor du Paprika-Aufrufe änderst oder neue brauchst: `docs/paprika-api.md`.
- Bei Release, LXC, Cloudflare, Kalender, Home Assistant oder LLM-Anbindung: `docs/betrieb.md`.
- Bevor du ein neues Feature planst: `docs/offen.md`.
- Regeln fürs Review: `CODING_STANDARDS.md`.

## Domänensprache

| Begriff | Bedeutung | Code |
|---|---|---|
| Rezept | Aus Paprika importiert, Paprika ist die Quelle der Wahrheit | `Recipe` |
| Kategorie / Tag | Paprika-Kategorie, wird als Tag gelesen (case-insensitiv) | `Recipe.tags` |
| Thementag | Wochentag mit Protein-Thema, z. B. Mi = Fisch | `DayRule` |
| Kinder-OK | Tag für Rezepte, die die Jungs essen | `config.kid_tag` |
| Neu | Tag für Kandidaten, die noch ausprobiert werden | `config.new_tag` |
| Neu-Slot | Ein Tag pro Woche, an dem ein Neu-Rezept geplant wird | `config.new_slot` |
| Cooldown | Ein Rezept kommt frühestens nach N Wochen wieder | `config.cooldown_weeks` |
| Plan | Eine Woche Mittagessen, Status `entwurf` oder `freigegeben` | `WeekPlan` |
| Mahlzeit | Ein geplanter Tag im Plan | `Meal` |
| Verschieben | Zwei Tage einer Woche tauschen ihre Gerichte; das Thema bleibt am Wochentag, die Mahlzeit vermerkt „verschoben von …“; auch im freigegebenen Plan, nicht für vergangene Tage; freie Tage tauschen mit; ein Reste-Tag zieht mit seinem Gericht mit | `move` |
| Anderes Gericht | Für einen Tag ein anderes Rezept zum selben Thema würfeln (früher „Tauschen“) | `swap` |
| Rezept wählen | Ein bestimmtes Rezept von Hand auf einen Tag legen; bleibt beim Neu-Würfeln der Woche stehen; Cooldown, Kinder-OK und Abneigungen nur als Hinweis; ein von Hand gewähltes Neu-Rezept ersetzt den Neu-Slot der Woche | `choose`, `Meal.chosen` |
| Reste-Tag | Am Kochtag markiert („Reste morgen“): der nächste Tag mit Mittagessen isst die Reste, kein Kochen, kein Einkauf, sein Thema entfällt diese Woche; zählt nicht als eigene Mahlzeit; nur innerhalb der Woche | `set_leftovers`, `Meal.leftovers` |
| Themenwechsel | Ein Tag bekommt für eine Woche ein anderes Thema (oder „beliebig“); bleibt beim Neu-Würfeln stehen; am Neu-Slot-Tag wird das Neu-Rezept dann zum neuen Thema gesucht | `set_theme`, `themedays` |
| Rückgängig | Nimmt die letzte Planänderung einer Woche zurück (eine Stufe), nur solange die Woche seitdem unverändert ist; Löschen auf der Einkaufsliste ebenso | `undo`, `restore_items` |
| Im Wagen | Abgehakte Zutaten, unten auf der Einkaufsliste eingeklappt | |
| Nachtrag | Gekochte Mahlzeit, die nicht vom Planer kommt: „stattdessen“ ersetzt das geplante Gericht des Tages, an Tagen ohne Plan kommt sie dazu; zählt für Cooldown, Rückblick, Bewertung und Rückmeldung; auch als Freitext ohne Paprika-Rezept (dann nur Rückblick); Kalender und Home Assistant zeigen das tatsächlich Gekochte | `Cooked`, `with_cooked`, `cooked` |
| Rückmeldung | Nach einem Neu-Rezept: `ok` (in die Rotation) oder `nein` (nie wieder) | `Feedback` |
| Bewertung | Nach jedem Essen: gut / mittel / schlecht, eine pro Mahlzeit (wer bewertet hat, wird vermerkt); steuert die Gewichtung; „gut“ als allererste Bewertung macht ein Rezept Kinder-OK (auch in Paprika; ein Neu-Rezept kommt wie bisher in die Rotation), zurücknehmbar in der Meldung oder über den Kinder-OK-Schalter der Rezepte; Home Assistant fragt nach dem Mittagessen „Wie war's?“ mit Knöpfen | `ratings`, `rate`, `rating_tick` |
| Freier Tag | Kein Mittagessen (Urlaub, Feiertag, Kita, „Reste vom Vortag“ …); Feiertage automatisch, „Doch kochen“ überstimmt; nicht zu verwechseln mit dem Reste-Tag eines Gerichts | `skipdays`, `holidays.py` |
| Einkaufsliste | Zutaten der Woche ohne Vorräte, auf die Portionen umgerechnet, gleiche Zutaten zusammengeführt und nach Gang sortiert (mit LLM oder per Regeln); gelöschte Zutaten bleiben für die Woche weg, solange kein anderes Gericht sie braucht; zum ersten Mal gesammelt bei Freigabe (oder von Hand), danach sammelt sie sich bei jeder Änderung der Gerichte selbst neu | `shopping.py`, `shopping`, `shopping_removed` |
| Bring-Abgleich | Einkaufsliste ↔ Bring über Home Assistant; pro Eintrag `bring` 0/1/2 (nicht drin, offen, abgehakt), nur Änderungen werden geschickt | `push_bring`, `apply_bring_state` |
| Abneigung | Zutat, die die Kinder nicht mögen (Einstellungen); solche Rezepte plant Tischlein zuletzt | `dislikes`, `avoid` |
| Schneller Tag | Tag mit wenig Zeit: Gerichte bis 30 Minuten zuerst | `quickdays`, `quick` |
| Portionen | Pro geplantem Gericht, Standard `portionen` aus tischlein.toml; skaliert die Mengen | `config.portions`, `servings` |
| Idee | Vorschlag des LLM für ein neues Gericht; übernommen landet es mit „Neu“ in Paprika | `ideas.py`, `ideas` |
| Saison | Gemüse und Obst, das in Deutschland gerade Saison hat (Feld und Lager; Ganzjähriges, TK und Dose zählen nicht); gewichtet den Plan, schließt nie aus; sichtbar am Gericht, als Filter „Jetzt Saison“ und als Vorschläge beim Stöbern | `season.py`, `plan_weights` |
| Wunschliste | Rezepte mit „Nächste Woche einplanen“: Gibt es für die nächste Woche schon einen Entwurf, kommt das Rezept sofort hinein, sonst in den nächsten Entwurf einer kommenden Woche, am passenden Thementag, als von Hand gewählt | `wishes`, `add_wish` |
| Vorschlag | Das LLM schlägt für Mittagsgerichte ohne Thema ein Thema und ohne Kochzeit eine Zeit vor; die Familie bestätigt oder lehnt ab (wird dann nicht wieder gefragt); ein Thema geht als Kategorie nach Paprika, die Zeit bleibt als Schätzung in Tischlein | `proposals.py`, `proposals`, `cooktimes` |
| Stöbern | Suche bei Chefkoch (mindestens 4 Sterne, ohne Plus-Rezepte) über deren inoffizielle App-Schnittstelle; Treffer werden nicht gespeichert, ein Treffer öffnet die Vorschau eines Web-Rezepts | `search_web`, `webrecipe.search_results` |
| Entwurf von selbst | Freitags legt Tischlein den Entwurf der nächsten Woche an und meldet ihn mit „Freigeben“; sonntags erinnert es einmal; freigeben tut nur die Familie | `draft_tick`, `approve_tick` |
| Web-Rezept | Rezept von einer fremden Seite (z. B. Chefkoch), per Link geholt; nach einer Vorschau übernommen landet es mit „Neu“, Quelle und Foto (JPEG) in Paprika; gibt es die Quelle schon, wird nicht doppelt übernommen | `webrecipe.py`, `accept_web_recipe` |

## Planungsregeln

1. Für jeden Thementag wählt der Planer ein Rezept mit dem Thema-Tag.
2. Normale Tage: nur Kinder-OK-Rezepte. Gibt es keins, fällt er auf alle Rezepte des Themas
   zurück und vermerkt das als Grund.
3. Rezepte, die innerhalb des Cooldowns gekocht wurden (freigegebene Pläne), und Rezepte, die
   diese Woche schon drin sind, scheiden aus. Bleibt nichts übrig, nimmt er das am längsten
   nicht gekochte.
4. Unter den Kandidaten wählt er gewichtet zufällig nach Paprika-Bewertung (Gewicht = Sterne + 1).
5. Neu-Slot: ein Rezept mit Tag `Neu` und ohne Rückmeldung, das ein Mittagsgericht ist
   (Kategorie `Hauptspeisen` oder ein Protein-Thema), bevorzugt passend zum Thema. Gibt es keins,
   gilt die normale Regel. Der Hinweis „sichere Komponente dazu“ steht in der Mahlzeit.
6. Rückmeldungen überlagern Paprika-Tags lokal: `ok` macht ein Rezept Kinder-OK und nicht mehr
   neu, `nein` schließt es aus. Rückmeldungen überleben einen erneuten Import.
7. Freie Tage (Feiertage aus `feiertage_land`, von Hand markierte Tage) bekommen kein Gericht.
8. Bewertungen gewichten: gut = 1, mittel = 0, schlecht = −1, Faktor 1 + 0,6 × Mittelwert
   (0,4 bis 1,6) auf die Wahrscheinlichkeit. „Nie wieder“ setzt die Rückmeldung `nein`; „gut“ für
   ein Neu-Rezept setzt `ok`.
9. Abneigungen (Einstellungen): Rezepte, deren Zutaten eine davon nennen, nur wenn zum Thema nichts
   anderes übrig ist; der Grund steht in der Mahlzeit. Neu-Slot und KI-Ideen meiden sie ganz.
10. Schnelle Tage: zuerst Rezepte bis 30 Minuten (`total_time`) oder mit Kategorie `Schnell`, sonst
    die normale Regel mit Grund. Ein Entwurf wählt den Tag beim Umschalten neu.
11. Themenwechsel ersetzt das Thema eines Tages für eine Woche („Beliebig“ = jedes Mittagsgericht);
    der Neu-Slot sucht dann zum neuen Thema.
12. Von Hand gewählte Rezepte bleiben beim Neu-Würfeln stehen, mit ihrem Reste-Tag. Ein von Hand
    gewähltes Neu-Rezept füllt den Neu-Slot; der Slot-Tag bekommt dann ein normales Gericht.
13. Ein Reste-Tag gehört zum Gericht des Mittagessens davor; ändert sich das, wird er normal geplant.
14. Gekocht ist, was in einem freigegebenen Plan steht (ohne Reste-Tage) oder nachgetragen wurde;
    ein Nachtrag ersetzt das geplante Gericht seines Tages (Cooldown, Rückblick, Kalender).
15. Eine freigegebene Woche wird nicht neu gewürfelt (weder ganz noch ein Tag); ändern lässt sie
    sich nur gezielt: Verschieben, Rezept wählen, Thema, Reste, freier Tag, Nachtrag.
16. Saison: Rezepte mit Saisonzutat kommen 1,5-mal so wahrscheinlich; mit einer klaren Saisonzutat
    außerhalb ihrer Zeit (Spargel, Erdbeeren, Rhabarber, Bärlauch, Kürbis, Grünkohl, Pfifferlinge)
    nur mit 0,3. Der Faktor multipliziert sich mit dem aus den Bewertungen. Von Hand geht immer alles.
17. Wunschliste: Gewünschte Rezepte stehen im Entwurf einer kommenden Woche wie von Hand gewählt,
    am ersten freien Tag mit passendem Thema, sonst am ersten freien Tag; die laufende Woche lässt
    die Wunschliste in Ruhe.

## Architektur

```
src/tischlein/
  config.py    tischlein.toml laden → PlanConfig
  models.py    Recipe, Meal, WeekPlan, Feedback (Dataclasses, keine Logik mit I/O)
  paprika.py   .paprikarecipes lesen (ZIP mit gzip-JSON pro Rezept)
  paprika_sync.py  Client für die inoffizielle Paprika-Sync-API (Transport injizierbar)
  service.py   Anwendungsfälle für Web und CLI, from_env() baut alles aus Umgebungsvariablen;
               Einstellungen (Paprika-Login, Kalender), Tags schreiben, Bild-Cache
  tagging.py   Kategorie-Differenzen nach Paprika schreiben (CSV-Befehl und Oberfläche)
  views.py     View-Modelle für die Templates
  templates/   eine Seite pro Bereich (Jinja, serverseitig gerendert); _macros.html: Rezeptbild, Einkaufs-Formular;
               waehlen.html: Rezept wählen und Nachtrag; rezept-holen.html: Vorschau eines Web-Rezepts
  static/      tischlein.css; rezepte.js, ideen.js, einkauf.js = Alpine-Komponenten (Markup im Template);
               stay.js: Formulare mit data-stay schicken ohne Neuladen ab (keine springende Seite);
               menus.js: Dropdowns (details.free-menu) schließen sich gegenseitig, bei Klick daneben und Escape;
               auf dem Handy kommen sie als Blatt von unten (CSS);
               ziehen.js: einen Tag am Griff auf einen anderen ziehen tauscht die beiden (Verschieben);
               die Leiste hat drei Bereiche (Woche, Einkauf, Rezepte); „Neues finden“ (ideen.html: Stöbern, Link, Ideen)
               hängt unter Rezepte, der Rückblick unter der Woche;
               sw.js = Service Worker (unter /sw.js), nur damit die Einkaufsliste offline öffnet;
               manifest.webmanifest + icons/ für den Home-Bildschirm (display: browser, wegen Cloudflare Access);
               vendor/alpine-<version>.min.js, nur über `mise run vendor:alpine` aktualisieren
  planner.py   reine Funktion plan_week(...) – deterministisch über übergebenen Random
  store.py     SQLite (stdlib sqlite3), einzige Stelle mit SQL
  ics.py       ICS-Feed erzeugen (ohne Fremdbibliothek, floating local time)
  holidays.py  Feiertage bundesweit + pro Bundesland (Ostern selbst berechnet)
  prep.py      Vorbereitungshinweise aus Zutaten (Regeln: TK auftauen, einweichen, marinieren …)
  shopping.py  Einkaufsliste: Zutaten sammeln, Vorräte filtern, LLM-Prompt und -Antwort
  ideas.py     Rezeptideen: LLM-Prompt, Antwort prüfen, neues Paprika-Rezept bauen
  season.py    Saisonkalender für Deutschland, Saisonzutaten eines Rezepts, Gewichtungsfaktor
  proposals.py Vorschläge für Thema und Kochzeit: LLM-Prompt und -Antwort
  webrecipe.py Web-Rezepte: schema.org-Rezept oder LLM-Antwort lesen, Thema vorschlagen, Paprika-Rezept
               bauen; das Abrufen (nur öffentliche Adressen, 3 MB, 10 s) macht service.py
  llm.py       Client für OpenAI-kompatible Chat-API (z. B. LiteLLM), Transport injizierbar
  migrations/  Alembic-Revisionen; Store._migrate hebt die DB beim Start auf head
  app.py       FastAPI: Web-UI (Jinja), JSON-API, /kalender.ics
  cli.py       import | sync | plan | tags-anwenden | serve
```

Beim Umsetzen:
- Schemaänderungen nur als Alembic-Revision (`mise run db:revision -- -m "…"`).
- Alpine.js nur über `mise run vendor:alpine` aktualisieren.
- Reine Module (Liste in `tests/test_architecture.py`) bleiben ohne Datenbank, Web und Netzwerk.
