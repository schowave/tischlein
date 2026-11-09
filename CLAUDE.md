# Tischlein

Familien-Essensplan für Mittagessen. Tischlein rotiert bewährte, eiweißreiche Rezepte aus
Paprika nach einem Wochenschema (Thementage), plant pro Woche einen Neu-Slot und liefert den
Plan als Kalender-Abo (ICS), als JSON für Home Assistant und als kleine Web-Oberfläche.

Prinzip „Sous-Chef“: Tischlein schlägt vor, die Familie gibt frei. Nur freigegebene Pläne
landen im Kalender.

## Befehle

```bash
uv run pytest                                    # alle Tests
uv run tischlein import Rezepte.paprikarecipes   # Paprika-Export einlesen
uv run tischlein plan --woche naechste           # Plan erzeugen (Entwurf)
uv run tischlein serve                           # Web + API auf :8080
docker compose up -d --build                     # Container, Daten in ./data
```

Konfiguration: `tischlein.toml` (Wochenschema, Tags, Cooldown). Pfad über `TISCHLEIN_CONFIG`,
Datenbank über `TISCHLEIN_DB` (Default `data/tischlein.db`).

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
5. Neu-Slot: ein Rezept mit Tag `Neu` und ohne Rückmeldung, bevorzugt passend zum Thema. Gibt es
   keins, gilt die normale Regel. Der Hinweis „sichere Komponente dazu“ steht in der Mahlzeit.
6. Rückmeldungen überlagern Paprika-Tags lokal: `ok` macht ein Rezept Kinder-OK und nicht mehr
   neu, `nein` schließt es aus. Rückmeldungen überleben einen erneuten Import.

## Architektur

```
src/tischlein/
  config.py    tischlein.toml laden → PlanConfig
  models.py    Recipe, Meal, WeekPlan, Feedback (Dataclasses, keine Logik mit I/O)
  paprika.py   .paprikarecipes lesen (ZIP mit gzip-JSON pro Rezept)
  planner.py   reine Funktion plan_week(...) – deterministisch über übergebenen Random
  store.py     SQLite (stdlib sqlite3), einzige Stelle mit SQL
  ics.py       ICS-Feed erzeugen (ohne Fremdbibliothek, floating local time)
  app.py       FastAPI: Web-UI (Jinja), JSON-API, /kalender.ics
  cli.py       import | plan | serve
```

Regeln für den Code:
- `planner.py` und `paprika.py` bleiben frei von Datenbank und HTTP, damit sie direkt testbar sind.
- Neue Planungsregeln immer zuerst als Test in `tests/test_planner.py`.
- Bezeichner im Code englisch, Texte in UI, Kalender und Konfiguration deutsch.

## Integrationen

- **Apple-Kalender:** `https://<host>/kalender.ics` abonnieren. Enthält nur freigegebene Pläne.
- **Home Assistant:**
  - Kalender: Integration „Remote Calendar“ mit derselben ICS-URL.
  - Sensor: REST-Sensor auf `/api/heute` (Feld `gericht`).
  - Wöchentlicher Entwurf: HA-Automation ruft `POST /api/plaene/naechste/erzeugen` auf,
    Tischlein ruft danach optional `HA_WEBHOOK_URL` auf („Mahlzeit: Plan für nächste Woche ist da“).
- **Paprika:** Import über den Export „Paprika Recipe Format“. Rückschreiben in den Paprika
  Meal Planner ist offen (siehe unten).

## Betrieb

- Docker auf dem Proxmox, Port 8080, Volume `./data`.
- Keine eigene Authentifizierung. Von außen nur über Cloudflare Tunnel **mit Cloudflare Access**
  erreichbar machen; die ICS-URL für HA/Kalender über einen Access-Service-Token oder nur im LAN.

## Offen

- Rückschreiben in den Paprika Meal Planner: Paprika hat nur eine inoffizielle Sync-API
  (`paprikaapp.com/api/v2/sync/...`). Vor der Umsetzung als ADR entscheiden, ob sich das lohnt.
- Vorschläge für neue Rezepte (z. B. per Claude API anhand von Kinder-OK-Rezepten) – erst wenn
  die Rotation im Alltag läuft.
- Vorlieben und Abneigungen der Jungs als Ausschluss-Zutaten.
