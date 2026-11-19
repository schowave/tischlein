---
name: app-ansehen
description: Tischlein lokal im Browser ansehen und prüfen. Nutzen, wenn eine Änderung an Templates, CSS, JS oder einer Route in der echten Oberfläche gezeigt, fotografiert oder durchgeklickt werden soll.
---

Die Oberfläche lokal ansehen, mit den echten Daten aus `data/tischlein.db`.

1. `mise run app:start` startet den Server im Hintergrund und kehrt zurück, sobald er antwortet. Die
   Adresse steht in der Ausgabe (`http://localhost:8080`), das Log in `data/dev-server.log`. Läuft schon
   ein Server, wird er benutzt.
2. Im Browser öffnen: `CHROME_DEVTOOLS_AXI_SESSION=tischlein chrome-devtools-axi open <adresse>`, dann
   `screenshot <datei>` ins Scratchpad und das Bild lesen. Handybreite: `emulate --viewport 390x800x2,mobile`
   (`resize` ändert die Breite nicht). Zustände per `eval` prüfen statt aus dem Bild zu raten.
3. Fertig ist die Prüfung, wenn jede geänderte Ansicht in Desktop- und Handybreite gesehen wurde und
   `console --type error` leer ist.
4. `mise run app:stop` und `chrome-devtools-axi stop`.

Seiten: `/` Woche, `/einkauf`, `/rezepte` (Sammlung), `/ideen` (Neues finden), `/einstellungen`.

Der Server arbeitet auf der echten Sammlung und mit dem echten Paprika-Zugang aus `.env`. Ansehen und
Suchen ist frei; alles, was schreibt (nach Paprika übernehmen, Abgleich, Freigeben, Bring), nur auf
ausdrücklichen Wunsch. Für freie Versuche: Demo-Daten nach `scripts/demo.py`.
