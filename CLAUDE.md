# Tischlein

Familien-Essensplan für Mittagessen. Tischlein rotiert bewährte, eiweißreiche Rezepte aus
Paprika nach einem Wochenschema (Thementage), plant pro Woche einen Neu-Slot und liefert den
Plan als Kalender-Abo (ICS), als JSON für Home Assistant und als kleine Web-Oberfläche.

Prinzip „Sous-Chef“: Tischlein schlägt vor, die Familie gibt frei. Nur freigegebene Pläne
landen im Kalender.

## Befehle

Werkzeuge (Python, uv, ruff) und Abläufe verwaltet mise (`mise.toml`, `mise.lock`), CI und
Docker-Build nutzen dieselben Tasks. Befehle immer über mise ausführen.

```bash
mise run setup                                   # frischer Klon -> lauffähig
mise run check                                   # ruff + Tests, wie im CI
mise run serve                                   # Web + API auf :8080 (liest .env)
mise run docker:build                            # Image bauen (mise auch im Image)
uv run tischlein sync                            # Rezepte aus der Paprika-Cloud abgleichen
uv run tischlein import Rezepte.paprikarecipes   # alternativ: Paprika-Export einlesen
uv run tischlein plan --woche naechste           # Plan erzeugen (Entwurf)
uv run tischlein serve                           # Web + API auf :8080
docker compose up -d                             # Container aus mise run docker:build, Daten in ./data
```

Konfiguration: `tischlein.toml` (Wochenschema, Tags, Cooldown). Pfad über `TISCHLEIN_CONFIG`,
Datenbank über `TISCHLEIN_DB` (Default `data/tischlein.db`). Paprika-Zugang am besten über die
Seite **Einstellungen** (liegt in der Tabelle `meta`, gewinnt gegen die Umgebung); alternativ
`PAPRIKA_EMAIL`, `PAPRIKA_PASSWORD` (optional `PAPRIKA_USER_AGENT`) in `.env`, nie im Repo.

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
  views.py     View-Modelle für die Templates (Woche, Rezepte, Einstellungen)
  templates/   base, woche, rezepte, einstellungen (Jinja, serverseitig gerendert)
  static/      tischlein.css; rezepte.js = Alpine-Komponente der Rezepte-Seite (Markup im Template);
               vendor/alpine-<version>.min.js, nur über `mise run vendor:alpine` aktualisieren
  planner.py   reine Funktion plan_week(...) – deterministisch über übergebenen Random
  store.py     SQLite (stdlib sqlite3), einzige Stelle mit SQL
  ics.py       ICS-Feed erzeugen (ohne Fremdbibliothek, floating local time)
  app.py       FastAPI: Web-UI (Jinja), JSON-API, /kalender.ics
  cli.py       import | plan | serve
```

Regeln für den Code:
- Frontend ohne Build-Schritt: Jinja für alles, Alpine.js nur wo Zustand im Browser nötig ist
  (Rezepte-Seite). Kein CDN; Version und npm-Prüfsumme stehen im Task `vendor:alpine`.
- `Store` ist threadsicher (eine Sperre um jede Methode), weil der Server parallel anfragt.
- `planner.py` und `paprika.py` bleiben frei von Datenbank und HTTP, damit sie direkt testbar sind.
- Neue Planungsregeln immer zuerst als Test in `tests/test_planner.py`.
- Bezeichner im Code englisch, Texte in UI, Kalender und Konfiguration deutsch.

## Integrationen

- **Apple-Kalender:** geheimer Abo-Link `https://<host>/kalender/<token>.ics` aus den
  Einstellungen (Knopf „Auf diesem Gerät abonnieren“ öffnet `webcal://`). Enthält nur freigegebene
  Pläne; Name, Uhrzeit und Dauer stehen in den Einstellungen. „Neuen geheimen Link erstellen“
  macht alte Abos ungültig. `/kalender.ics` ohne Token nur im LAN bzw. hinter Access nutzen.
- **Home Assistant:**
  - Kalender: Integration „Remote Calendar“ mit derselben ICS-URL.
  - Sensor: REST-Sensor auf `/api/heute` (Feld `gericht`).
  - Wöchentlicher Entwurf: HA-Automation ruft `POST /api/plaene/naechste/erzeugen` auf,
    Tischlein ruft danach optional `HA_WEBHOOK_URL` auf („Mahlzeit: Plan für nächste Woche ist da“).
- **Paprika:** Primär Sync über die inoffizielle Cloud-API (`paprika_sync.py`):
  - Login `POST /api/v1/account/login/` (Form + Basic Auth) → Token.
    v2-Login lehnt Fremd-Clients ab („Unrecognized client“).
  - Lesen über `/api/v2/sync/...` mit `Bearer <token>`. Geprüft 2026-11: v2 nimmt nur Bearer,
    v1-Sync nur Basic Auth.
  - `sync/categories/`, `sync/recipes/` (uid + hash), `sync/recipe/<uid>/`.
    Rezepte verweisen per UID auf Kategorien.
  - Nur geänderte Rezepte (Hash) werden geladen, Rohdaten liegen in `paprika_cache`. Paprika
    sperrt IPs bei zu vielen Anfragen, daher 0,2 s Pause zwischen Abrufen.
  - `POST /api/plaene/.../erzeugen` synchronisiert vorher; schlägt das fehl, wird mit dem
    letzten Stand geplant und `sync_fehler` gemeldet.
  - Fallback: Upload des Exports „Paprika Recipe Format“.
  - Die API ist undokumentiert und kann sich ändern. Bei Fehlern zuerst gegen
    github.com/johnwbyrd/kappari (Protokoll-Doku) abgleichen.

## Betrieb

- Docker auf dem Proxmox, Port 8080, Volume `./data`.
- Keine eigene Authentifizierung. Von außen nur über Cloudflare Tunnel **mit Cloudflare Access**
  erreichbar machen. Für den Pfad `/kalender/*` eine Access-Bypass-Regel anlegen: Der
  iPhone-Kalender kann sich nicht anmelden, der Link ist durch seinen geheimen Schlüssel geschützt.
- `tischlein serve` wertet `X-Forwarded-Proto`/`Host` aus, damit Abo-Links hinter dem Tunnel die
  öffentliche https-Adresse tragen.
- Rezeptfotos werden beim ersten Anzeigen nach `data/bilder/` geladen. Paprikas Bild-Links sind
  nur kurz gültig; abgelaufene werden über einen Rezeptabruf erneuert (seriell, 0,25 s Pause).

## Offen

- Rückschreiben freigegebener Pläne in den Paprika Meal Planner: `POST /api/v1/sync/meals/`,
  multipart-Feld `data` = gzip-JSON-Liste von `{uid, recipe_uid, date "YYYY-MM-DD 00:00:00",
  type, name, deleted, order_flag}`. Welcher `type` Mittagessen ist, vorher prüfen.
- Vorschläge für neue Rezepte (z. B. per Claude API anhand von Kinder-OK-Rezepten) – erst wenn
  die Rotation im Alltag läuft.
- Vorlieben und Abneigungen der Jungs als Ausschluss-Zutaten.
