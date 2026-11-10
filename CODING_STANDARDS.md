# Coding Standards

Regeln für das Review. Was sich mechanisch prüfen lässt, prüfen `mise run check` und
`tests/test_architecture.py`; hier stehen nur Ermessensfragen.

## Sprache

- Bezeichner, Kommentare und Docstrings im Code englisch.
- Alles, was die Familie sieht, deutsch: Texte in Templates, Fehlermeldungen in Exceptions, die
  bis in die Oberfläche reichen, Kalendereinträge, `tischlein.toml`, Commit-Nachrichten.

## Domäne

- Neue Planungsregeln kommen zuerst als Test in `tests/test_planner.py`, dann in `planner.py`,
  dann in die Planungsregeln der `CLAUDE.md`.
- Begriffe aus der Domänensprache in `CLAUDE.md` heißen überall gleich: im Code, in der
  Oberfläche, in Tests. Ein neuer Begriff kommt in die Tabelle.
- Paprika ist die Quelle der Wahrheit für Rezepte und Kategorien. Lokale Daten überlagern nur
  (Rückmeldungen, Bewertungen, freie Tage) und überleben einen erneuten Abgleich.

## Schnittstellen nach außen

- Jeder Schreibzugriff auf Paprika sichert vorher die Fassung des Rezepts
  (`paprika-backup-*.jsonl`) und ändert nur die Differenz auf frisch geladenen Daten.
- LLM-Aufrufe bleiben optional: Jede Funktion hat einen Weg ohne LLM oder sagt klar, dass sie
  eins braucht. An das LLM gehen nur Rezeptnamen, Zutaten und Vorlieben.
- Zugangsdaten (Paprika, LLM) erscheinen nie in Antworten, Logs oder Templates.

## Datenbank

- Schemaänderungen nur als Alembic-Revision (`mise run db:revision -- -m "…"`); eine Revision
  muss auf einer Datenbank im Stand der Vorgänger-Revision laufen, die schon Daten enthält.
- SQL steht nur in `store.py`; jede öffentliche Methode trägt `@_locked`.

## Oberfläche

- Serverseitig gerendert mit Jinja; Alpine.js nur, wo Zustand im Browser nötig ist. Kein CDN,
  kein Build-Schritt.
- Neue Seiten folgen den Tokens in `static/tischlein.css` und dem Stil „editorial-greeting“
  (`.taste-skill/direction.json`): eine Akzentfarbe, nur für Kinder-OK und den Fortschritt.
