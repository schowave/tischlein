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
| Rückmeldung | Nach einem Neu-Rezept: `ok` (in die Rotation) oder `nein` (nie wieder) | `Feedback` |
| Bewertung | Nach jedem Essen: gut / mittel / schlecht, pro Tag und Person; steuert die Gewichtung | `ratings` |
| Freier Tag | Kein Mittagessen (Urlaub, Feiertag, Kita …); Feiertage automatisch, „Doch kochen“ überstimmt | `skipdays`, `holidays.py` |
| Einkaufsliste | Zutaten der Woche ohne Vorräte, auf die Portionen umgerechnet, gleiche Zutaten zusammengeführt und nach Gang sortiert (mit LLM oder per Regeln) | `shopping.py`, `shopping` |
| Abneigung | Zutat, die die Kinder nicht mögen (Einstellungen); solche Rezepte plant Tischlein zuletzt | `dislikes`, `avoid` |
| Schneller Tag | Tag mit wenig Zeit: Gerichte bis 30 Minuten zuerst | `quickdays`, `quick` |
| Portionen | Pro geplantem Gericht, Standard `portionen` aus tischlein.toml; skaliert die Mengen | `config.portions`, `servings` |
| Idee | Vorschlag des LLM für ein neues Gericht; übernommen landet es mit „Neu“ in Paprika | `ideas.py`, `ideas` |

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
  templates/   eine Seite pro Bereich (Jinja, serverseitig gerendert); _macros.html: Rezeptbild, Einkaufs-Formular
  static/      tischlein.css; rezepte.js, ideen.js, einkauf.js = Alpine-Komponenten (Markup im Template);
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
  llm.py       Client für OpenAI-kompatible Chat-API (z. B. LiteLLM), Transport injizierbar
  migrations/  Alembic-Revisionen; Store._migrate hebt die DB beim Start auf head
  app.py       FastAPI: Web-UI (Jinja), JSON-API, /kalender.ics
  cli.py       import | sync | plan | tags-anwenden | serve
```

Beim Umsetzen:
- Schemaänderungen nur als Alembic-Revision (`mise run db:revision -- -m "…"`).
- Alpine.js nur über `mise run vendor:alpine` aktualisieren.
- Reine Module (Liste in `tests/test_architecture.py`) bleiben ohne Datenbank, Web und Netzwerk.
