# Paprika-Schnittstelle

Paprika hat keine offizielle API. Tischlein nutzt die inoffizielle Sync-API der Apps
(`paprika_sync.py`, Transport injizierbar). Alles hier ist gegen ein echtes Konto geprüft
(Stand 2026-11), sofern nicht anders vermerkt.

## Anmeldung und Lesen

- Login `POST /api/v1/account/login/` (Form `email`, `password` plus Basic Auth) → `result.token`.
  Der v2-Login lehnt Fremd-Clients ab („Unrecognized client“).
- Lesen über `/api/v2/sync/...` mit `Authorization: Bearer <token>`. v2 nimmt nur Bearer,
  v1-Sync nur Basic Auth.
- `sync/categories/` (uid, name, parent_uid, order_flag), `sync/recipes/` (uid + hash),
  `sync/recipe/<uid>/`. Rezepte verweisen per UID auf Kategorien; `in_trash` markiert gelöschte.
- `sync/status/` liefert Änderungszähler (`recipes`, `categories`, `meals` …); der automatische
  Abgleich gleicht nur ab, wenn sich `recipes` oder `categories` geändert haben.
- Antworten sind `{"result": …}`; der Client akzeptiert auch blankes JSON und gzip.

## Schreiben

- Rezept anlegen oder ändern: `POST /api/v2/sync/recipe/<uid>/`, multipart-Feld `data` =
  gzip-JSON des **ganzen** Rezepts; `hash` = SHA-256 (groß geschrieben) über das JSON ohne
  `hash` mit `sort_keys`. Leere Foto-Felder als `null`, `photo_url` weglassen. Neue Rezepte
  bekommen eine UUID in Großbuchstaben (Anlegen nur gegen Fakes getestet).
- Kategorie anlegen: `POST /api/v2/sync/categories/` mit gzip-JSON-**Liste**
  `[{uid, name, parent_uid, order_flag, deleted}]`.
- Papierkorb: Rezept mit `in_trash: true` zurückschreiben.
- Danach `POST /api/v2/sync/notify/`, damit die Apps abgleichen.
- Vor jedem Schreiben die Fassung sichern (`paprika-backup-*.jsonl`) und nur die Differenz auf
  frisch geladenen Daten anwenden (`tagging.py`).

## Grenzen

- Paprika sperrt IPs bei zu vielen Anfragen (~300/min): nur geänderte Rezepte laden (Hash), 0,2 s
  Pause zwischen Abrufen, Rohdaten in `paprika_cache`.
- Bild-Links (`photo_url`) sind signiert und nur kurz gültig; `recipe_photo` lädt sie beim
  ersten Anzeigen nach `data/bilder/` und erneuert abgelaufene über einen Rezeptabruf.
- Bei Fehlern zuerst gegen github.com/johnwbyrd/kappari (Protokoll-Doku) abgleichen.
- Noch nicht genutzt: Meal Planner `POST /api/v1/sync/meals/`, multipart `data` = gzip-JSON-Liste
  `{uid, recipe_uid, date "YYYY-MM-DD 00:00:00", type, name, deleted, order_flag}`; welcher `type`
  Mittagessen ist, vorher prüfen.
