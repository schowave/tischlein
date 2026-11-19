# Tischlein

Familien-Essensplan für Mittagessen. Tischlein rotiert bewährte, eiweißreiche Rezepte aus
Paprika nach einem Wochenschema (Thementage), plant pro Woche einen Neu-Slot und liefert den
Plan als Kalender-Abo (ICS), als JSON für Home Assistant und als kleine Web-Oberfläche.

Prinzip „Sous-Chef“: Tischlein schlägt vor, die Familie gibt frei. Nur freigegebene Pläne
landen im Kalender.

## Wo was steht

- Abläufe (setup, check, serve, Docker, Migrationen): `mise tasks`. Werkzeuge und Versionen
  verwaltet mise; immer über mise ausführen. Konfiguration und Umgebungsvariablen: README.
- Begriffe (Plan, Mahlzeit, Neu-Slot, Reste-Tag, Nachtrag …) und die Planungsregeln, bevor du Planer, Plan,
  Einkaufsliste oder Texte der Oberfläche änderst: `docs/domaene.md`. Neue Begriffe und Regeln dort eintragen.
- Bevor du Paprika-Aufrufe änderst oder neue brauchst: `docs/paprika-api.md`.
- Bevor du eine Stöber-Quelle (Chefkoch, Eat Smarter, Cookidoo) änderst oder eine ergänzt: `docs/stoebern-quellen.md`.
- Die App im Browser ansehen: `mise run app:start`, danach `mise run app:stop`.
- Bei Release, LXC, Cloudflare, Kalender, Home Assistant oder LLM-Anbindung: `docs/betrieb.md`.
- Bevor du ein neues Feature planst: `docs/offen.md`.
- Regeln fürs Review: `CODING_STANDARDS.md`.

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
               die Leiste hat vier Bereiche (Woche, Einkauf, Rezepte, Neues finden = ideen.html: Stöbern, Link, Ideen)
               der Rückblick hängt unter der Woche;
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
