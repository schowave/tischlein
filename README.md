# Tischlein

Familien-Essensplan fürs Mittagessen. Tischlein plant jede Woche eiweißreiche Gerichte aus euren
[Paprika](https://www.paprikaapp.com)-Rezepten nach Thementagen. Es achtet darauf, was die Kinder
essen, schlägt pro Woche ein neues Gericht vor und stellt den freigegebenen Plan als Kalender aufs
iPhone und in Home Assistant.

Das Prinzip ist „Sous-Chef“: Tischlein schlägt vor, die Familie gibt frei.

## Inhalt

- [Funktionen](#funktionen)
- [Schnellstart mit Docker](#schnellstart-mit-docker)
- [Einrichtung](#einrichtung)
- [Konfiguration](#konfiguration)
- [Bedienung über die Kommandozeile](#bedienung-über-die-kommandozeile)
- [HTTP-API](#http-api)
- [Releases und automatisches Deployment](#releases-und-automatisches-deployment)
- [Betrieb auf dem Proxmox hinter Cloudflare](#betrieb-auf-dem-proxmox-hinter-cloudflare)
- [Daten und Sicherungen](#daten-und-sicherungen)
- [Entwicklung](#entwicklung)
- [Bekannte Grenzen](#bekannte-grenzen)

## Funktionen

- **Wochenplan nach Thementagen**, z. B. Mo Hähnchen, Di Hülsenfrüchte, Mi Fisch, Do Eier/Quark,
  Fr Pasta/Reis. Auf jeden Thementag kommt ein Rezept mit der passenden Kategorie.
- **Kinder-OK zuerst:** An normalen Tagen nimmt der Plan nur Gerichte, die die Kinder essen.
  „Vorrat pro Thema“ zeigt, wo noch welche fehlen.
- **Rotation statt Wiederholung:** Ein Gericht kommt frühestens nach zwei Wochen wieder.
  Gut bewertete Rezepte kommen öfter dran.
- **Neu-Slot:** einmal pro Woche ein Rezept mit der Kategorie „Neu“, nur Hauptspeisen. Nach dem
  Essen entscheidet die Familie: „Hat geschmeckt“ nimmt es in die Rotation, „Nie wieder“ wirft es
  raus.
- **Freigabe:** Einzelne Tage tauschen, die ganze Woche neu würfeln, freigeben. Nur freigegebene
  Wochen landen im Kalender.
- **Rezeptverwaltung** mit Foto, Zutaten und Kinder-OK-Schalter. Kategorien lassen sich direkt
  setzen, Rezepte in den Papierkorb legen. Alles wird sofort nach Paprika zurückgeschrieben.
- **Kalender-Abo** für iPhone und Mac über einen geheimen Link, dazu **Home Assistant**
  (Sensor „Mittagessen heute“, Plan per Automation).
- **Freie Tage:** Feiertage (bundesweit und pro Bundesland) automatisch, dazu Urlaub, Kita oder
  Auswärtsessen pro Tag.
- **Bewertung nach jedem Essen:** gut, ging so, nie wieder. Lieblingsgerichte kommen öfter.
- **Einkaufsliste:** aus dem Wochenplan, ohne Vorräte, mit LLM zusammengefasst und nach Gang
  sortiert, zum Abhaken am Handy.
- **Ideen:** neue Gerichte vom LLM, passend zu dem, was ihr mögt; per Klick nach Paprika.
- **Vorbereitung:** „Morgen gibt's Lachs, Tiefkühlware in den Kühlschrank“ am Vorabend über Home
  Assistant.
- **Rückblick:** was gekocht wurde, Themen, Bewertungen, Lieblinge, die lange nicht dran waren.
- **Einstellungen im Browser:** Paprika-Konto, LLM (OpenAI-kompatibel, z. B. LiteLLM), Kalender,
  Namen der angemeldeten Personen.

## Schnellstart mit Docker

Voraussetzungen: [mise](https://mise.jdx.dev), Docker mit Compose und ein Paprika-Konto mit
aktivierter Cloud-Synchronisation.

```bash
git clone git@github.com:schowave/tischlein.git
cd tischlein
mise trust && mise run docker:build
mkdir -p data && sudo chown 10001 data   # der Container läuft als UID 10001
docker compose up -d
```

Danach `http://<host>:8080` öffnen. Die Daten liegen im Ordner `./data`.

## Einrichtung

1. **Paprika verbinden:** unter **Einstellungen → Paprika-Konto** E-Mail und Passwort eintragen.
   Tischlein prüft die Anmeldung und gleicht danach die Rezepte im Hintergrund ab. Der erste
   Abgleich dauert einige Minuten.
2. **Kategorien prüfen:** Die Themen aus `tischlein.toml` müssen als Paprika-Kategorien existieren.
   In der Rezeptverwaltung lassen sie sich anlegen und zuordnen, dort setzt ihr auch Kinder-OK.
3. **Woche planen:** auf der Startseite „Woche planen“, einzelne Tage tauschen, dann „Woche
   freigeben“.
4. **Kalender aufs iPhone:** unter **Einstellungen → Kalender auf dem iPhone** den Knopf „Auf
   diesem Gerät abonnieren“ am iPhone öffnen. Das iPhone fragt, in welchem Konto der Kalender
   liegen soll, iCloud bringt ihn auf alle Geräte.
5. **Home Assistant** (optional):

   ```yaml
   # configuration.yaml
   rest:
     - resource: http://tischlein:8080/api/heute
       scan_interval: 1800
       sensor:
         - name: Mittagessen heute
           value_template: "{{ value_json.gericht }}"

   rest_command:
     tischlein_plan_erzeugen:
       url: http://tischlein:8080/api/plaene/naechste/erzeugen
       method: post
   ```

   Eine Automation, z. B. samstags um 10:00, ruft `rest_command.tischlein_plan_erzeugen` auf. Den
   Kalender bindet ihr über die Integration „Remote Calendar“ mit dem Abo-Link ein.

Ohne Paprika-Konto lassen sich Rezepte auch per Export einlesen: in Paprika unter Einstellungen →
Import/Export im Paprika-Format exportieren und die Datei auf der Startseite hochladen.

## Konfiguration

### `tischlein.toml`

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `plan.kinder_tag` | `Kinder-OK` | Kategorie für Gerichte, die die Kinder essen |
| `plan.neu_tag` | `Neu` | Kategorie für Rezepte zum Ausprobieren |
| `plan.hauptspeise_tag` | `Hauptspeisen` | Nur solche Gerichte (oder mit Thema) kommen in den Neu-Slot |
| `plan.neu_slot` | `do` | Wochentag des Neu-Slots, `""` schaltet ihn ab |
| `plan.cooldown_wochen` | `2` | Mindestabstand, bevor ein Gericht wiederkommt |
| `plan.weitere_themen` | `["Fleisch"]` | Themen ohne eigenen Tag, für die Rezeptverwaltung |
| `plan.feiertage_land` | `""` | Bundesland für Landesfeiertage, z. B. `"BW"`; leer = nur bundesweite |
| `abgleich.alle_minuten` | `15` | Automatische Prüfung auf Änderungen in Paprika, `0` = aus |
| `einkauf.vorrat` | Salz, Pfeffer, Öl … | Was nie auf die Einkaufsliste kommt |
| `erinnerung.uhrzeit` | `"18:00"` | Vorabend-Meldung an Home Assistant, `""` = aus |
| `[[tage]]` | Mo–Fr | Ein Eintrag pro Thementag: `tag = "mo"`, `thema = "Hähnchen"` |

Kalendername, Uhrzeit und Dauer stellt ihr unter **Einstellungen** ein.

### Umgebungsvariablen

| Variable | Standard | Bedeutung |
|---|---|---|
| `TISCHLEIN_DB` | `data/tischlein.db` | Pfad zur SQLite-Datenbank |
| `TISCHLEIN_CONFIG` | `tischlein.toml` | Pfad zur Konfiguration |
| `PAPRIKA_EMAIL`, `PAPRIKA_PASSWORD` | – | Alternative zu den Einstellungen; ein dort eingetragenes Konto hat Vorrang |
| `PAPRIKA_USER_AGENT` | `Tischlein/0.1` | Kennung gegenüber Paprika |
| `HA_WEBHOOK_URL` | – | Wird nach jedem automatisch erzeugten Plan aufgerufen |

Für Docker gehören sie in eine `.env`-Datei, Vorlage: [`.env.example`](.env.example). Die `.env`
wird nie eingecheckt.

## Bedienung über die Kommandozeile

Alle Befehle laufen in der von mise verwalteten Umgebung (`mise exec --` davor oder mise in der
Shell aktivieren):

```bash
uv run tischlein sync                                   # Rezepte mit Paprika abgleichen
uv run tischlein plan --woche naechste [--freigeben]    # Plan erzeugen
uv run tischlein import Rezepte.paprikarecipes          # Export einlesen
uv run tischlein tags-anwenden vorschlaege.csv          # Kategorien aus CSV, ohne --ausfuehren nur Probelauf
uv run tischlein serve [--port 8080]                    # Web-Oberfläche und API
```

## HTTP-API

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/api/heute`, `/api/morgen` | Gericht, Vorbereitung, freier Tag, für Home Assistant |
| `GET` | `/api/einkauf?woche=…` | Einkaufsliste der Woche |
| `GET` | `/api/plan?woche=aktuell\|naechste\|JJJJ-MM-TT` | Wochenplan als JSON |
| `POST` | `/api/plaene/{woche}/erzeugen` | Paprika abgleichen und einen Entwurf erzeugen |
| `POST` | `/api/sync` | Nur abgleichen |
| `GET` | `/api/rezepte`, `/api/rezepte/{uid}` | Rezepte, Zutaten und Kategorien |
| `POST` | `/api/rezepte/{uid}/tags` | `{"hinzufuegen": [...], "entfernen": [...]}`, schreibt nach Paprika |
| `POST` | `/api/rezepte/{uid}/papierkorb`, `…/wiederherstellen` | Papierkorb in Paprika |
| `GET` | `/kalender/{token}.ics` | Kalender-Abo mit geheimem Schlüssel |
| `GET` | `/healthz` | Health-Check |

## Releases und automatisches Deployment

```
GitHub-Release vX.Y.Z ──▶ Action „Release“ ──▶ ghcr.io/schowave/tischlein:X.Y.Z, X.Y, latest
                                                        │
LXC auf dem Proxmox: tischlein-update.timer (alle 5 Min) ◀┘ holt latest, startet neu,
                     prüft /healthz, bei Fehler zurück auf die vorherige Version
```

1. **Release erstellen:** auf GitHub ein Release mit Tag `vX.Y.Z` veröffentlichen. Die Action prüft
   wie das CI, baut das Image über `mise run docker:publish` und testet das veröffentlichte Image.
2. **Ausrollen passiert von selbst:** Der Timer im LXC zieht `latest`. Er startet den Container nur
   neu, wenn sich das Image geändert hat, und wartet, bis `/healthz` die neue Version meldet.
   Sonst setzt er die vorherige Version wieder in Betrieb. Mit `HA_WEBHOOK_URL` meldet er
   erfolgreiche Updates an Home Assistant.
3. **Welche Version läuft:** unten in der Seitenleiste oder `curl http://<lxc>:8080/healthz`.

Der LXC braucht keinen offenen Port nach außen und GitHub keinen Zugang ins Heimnetz.

**Einmalige Einrichtung des LXC** (Debian mit Docker, z. B. über die community-scripts):

```bash
# im LXC als root
git clone https://github.com/schowave/tischlein.git /tmp/tischlein   # oder deploy/lxc/ kopieren
bash /tmp/tischlein/deploy/lxc/install.sh
```

Solange das Paket auf ghcr.io privat ist, braucht der LXC einmalig ein `docker login ghcr.io` mit
einem Token, das nur `read:packages` darf.

## Betrieb auf dem Proxmox hinter Cloudflare

- Container im LXC unter `/opt/tischlein` (siehe oben), Port 8080, Daten in `/opt/tischlein/data`.
- Tischlein hat keine eigene Anmeldung. Von außen nur über **Cloudflare Tunnel mit Cloudflare
  Access** erreichbar machen.
- Für den Pfad `/kalender/*` eine **Access-Bypass-Regel** anlegen. Der iPhone-Kalender kann sich
  nicht anmelden, der Link ist durch seinen geheimen Schlüssel geschützt. Wird er bekannt, erzeugt
  ihr unter Einstellungen einen neuen.
- Tischlein wertet `X-Forwarded-Proto` und `Host` aus, damit Abo-Links hinter dem Tunnel die
  öffentliche https-Adresse tragen.

## Daten und Sicherungen

Alles liegt in `./data`. Sichert den Ordner mit, z. B. über Proxmox Backup:

| Datei | Inhalt |
|---|---|
| `tischlein.db` | Pläne, Rückmeldungen, Einstellungen (inklusive Paprika-Zugang im Klartext), Rezept-Cache |
| `bilder/` | Lokal zwischengespeicherte Rezeptfotos |
| `paprika-backup-*.jsonl` | Fassungen der Rezepte vor jeder Änderung durch Tischlein |

Weil der Paprika-Zugang in der Datenbank steht, sollte `./data` nur für euch lesbar sein.

## Entwicklung

Einzige Voraussetzung ist [mise](https://mise.jdx.dev). Es installiert Python, uv und ruff in den
Versionen aus `mise.toml`, abgesichert über die Prüfsummen in `mise.lock`. Die Python-Abhängigkeiten
kommen strikt aus `uv.lock`. CI und Docker-Build nutzen dieselben Tasks, ein grünes
`mise run check` lokal ist also ein grünes CI.

```bash
mise trust && mise run setup   # Werkzeuge und Abhängigkeiten
mise run check                 # ruff und Tests, wie im CI
mise run serve                 # lokal auf :8080, liest .env falls vorhanden
mise run docker:build          # Image bauen
mise tasks                     # alle Tasks
```

Versionen aktualisieren: Version in `mise.toml` anheben, dann `mise lock --platform
linux-x64,linux-arm64,macos-arm64` für die Werkzeuge und `mise exec -- uv lock --upgrade` für die
Python-Abhängigkeiten.

Domänensprache, Planungsregeln und Aufbau: [`CLAUDE.md`](CLAUDE.md). Paprika-Schnittstelle:
[`docs/paprika-api.md`](docs/paprika-api.md), Betrieb: [`docs/betrieb.md`](docs/betrieb.md), Review-Regeln:
[`CODING_STANDARDS.md`](CODING_STANDARDS.md). Neue Planungsregeln kommen immer zuerst als Test in
`tests/test_planner.py`.

## Bekannte Grenzen

- Paprika hat **keine offizielle Schnittstelle**. Tischlein nutzt die inoffizielle Sync-API der
  Apps, sie kann sich jederzeit ändern. Tischlein ist nicht mit Paprika verbunden oder von Paprika
  unterstützt.
- Die Paprika-Server sperren IPs bei zu vielen Anfragen. Tischlein lädt deshalb nur geänderte
  Rezepte und macht Pausen zwischen den Abrufen.
- Freigegebene Pläne werden noch nicht in den Meal Planner von Paprika geschrieben.
