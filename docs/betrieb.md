# Betrieb

## Release und Auto-Update

- GitHub-Release `vX.Y.Z` → `.github/workflows/release.yaml` → `mise run docker:publish` →
  `ghcr.io/schowave/tischlein:{X.Y.Z,X.Y,latest}`. Die Version steckt im Image
  (`TISCHLEIN_VERSION`) und steht in `/healthz` und der Seitenleiste. Vorher die Version in
  `pyproject.toml` anheben.
- Ablauf eines Releases: Version in `pyproject.toml` anheben, `uv lock`, direkt auf `main` committen
  (kein Feature-Branch; Betreff wie „Thema: was sich ändert, Version X.Y.Z“), pushen, dann
  `gh release create vX.Y.Z --title vX.Y.Z` mit Notizen unter „## Neu“ und „## Geändert“, je Punkt
  ein fettes Stichwort und ein Satz für die Familie. Fertig ist es, wenn der Workflow „Release“ grün ist.
- Statische Dateien tragen eine Marke ihres Inhalts (`asset()` in `app.py`); eine reine CSS- oder
  JS-Änderung braucht für den Browser-Cache keinen Versionssprung.
- LXC 104 auf dem Proxmox (`deploy/proxmox/create-lxc.sh`, eingerichtet mit `deploy/lxc/install.sh`):
  `/opt/tischlein/compose.yaml`, `tischlein-update.timer` alle 5 Minuten → `tischlein-update.sh`
  zieht `latest`, startet nur bei neuem Image neu, prüft `/healthz` auf die neue Version und rollt
  sonst zurück. Pull statt Push: kein offener Port, kein Zugang von GitHub ins Heimnetz.
- Die Datenbank im LXC hebt Alembic beim Start an.

## Zugriff von außen

- Keine eigene Anmeldung. Von außen nur über Cloudflare Tunnel (LXC 102) **mit Cloudflare
  Access**, Anwendung `familie-dienste`. Für `/kalender/*` eine Bypass-Anwendung: Der
  iPhone-Kalender kann sich nicht anmelden, der geheime Schlüssel im Link schützt ihn.
  Einrichtung und Fallen: homelab-Repo, `docs/betrieb/cloudflare-access.md`.
- `tischlein serve` wertet `X-Forwarded-Proto`/`Host` aus, damit Abo-Links die öffentliche
  https-Adresse tragen.
- Wer angemeldet ist, kommt aus `Cf-Access-Authenticated-User-Email` (nur Anzeige und Zuordnung,
  keine Berechtigung; im LAN ohne Cloudflare ist niemand angemeldet). Namen unter
  Einstellungen → Personen.

## Integrationen

- **Kalender:** geheimer Abo-Link `/kalender/<token>.ics` aus den Einstellungen (`webcal://`),
  nur freigegebene Pläne. „Neuen geheimen Link erstellen“ macht alte Abos ungültig.
  `/kalender.ics` ohne Schlüssel nur im LAN.
- **Home Assistant:** Kalender über „Remote Calendar“ mit dem Abo-Link; REST-Sensoren auf
  `/api/heute` und `/api/morgen` (`gericht`, `vorbereitung`, `frei`, `grund`); Automation ruft
  `POST /api/plaene/naechste/erzeugen` auf. Mit `HA_WEBHOOK_URL` meldet Tischlein neue Pläne und
  um `[erinnerung] uhrzeit` „Morgen gibt's …“ samt Vorbereitung (nur freigegebene Pläne, einmal
  am Tag). Trägt jemand von Hand etwas in die Einkaufsliste ein, kommt
  „<Name> hat Kaffee (500 g) auf die Liste gesetzt.“ mit `art: einkauf`, `von`, `eintrag`, `menge`.
  Nach dem Mittagessen (`[erinnerung] bewertung`) kommt „Wie war …?“ mit `art: bewertung` und `datum`;
  freitags (`entwurf`) legt Tischlein den Entwurf der nächsten Woche an und meldet ihn mit `art: entwurf`
  und `woche`, sonntags (`freigabe`) erinnert es einmal mit derselben Art. `notify_tischlein` hängt an
  beide Knöpfe; `tischlein_antwort` schickt den Tap an `POST /api/bewertung` (`datum`, `urteil`:
  gut, mittel, nie) bzw. `POST /api/plaene/freigeben` (`woche`).
  Gibt jemand eine Woche frei, kommt „<Name> hat die Woche ab 16.11. freigegeben: Mo …“ mit `art: freigabe`.
  **Bring-Übergabe** (Liste unter Einstellungen → Bring): „An Bring übergeben“ bzw. „Bring aktualisieren“
  in der Einkaufsliste schickt den Unterschied zur letzten Übergabe der Woche als `art: bring_sync`
  (`offen` [{name, menge}]: neu oder andere Menge, `weg` [name]: abgehakt, gelöscht oder beim Neu-Sammeln
  weggefallen, `liste`, `von`). `notify_tischlein` im homelab-Repo legt an oder ändert (`todo.add_item`,
  `todo.update_item`) und entfernt (`todo.remove_item`). Nur diese Richtung: Aus Bring kommt nichts zurück,
  von selbst geht nichts hinaus. Welche Listen es gibt, meldet HA beim Start und stündlich an
  `POST /api/bring/listen`. Abgleich über den Namen (Zwiebel = Zwiebeln).
  Fürs iPhone: Automation mit Webhook-Trigger, Aktion `notify.mobile_app_<gerät>` mit
  `message: "{{ trigger.json.nachricht }}"` (Companion-App). Wer den Eintrag selbst gemacht hat,
  lässt sich über `trigger.json.von` ausfiltern.
- **LLM:** OpenAI-kompatibel (z. B. LiteLLM), in den Einstellungen; Tischlein ruft
  `<Basis-URL>/chat/completions` mit `response_format: json_object` und ausdrücklichem `max_tokens`
  (Standard 8000, Einkaufsliste 16000): Ohne kappt LiteLLM bei 4096, und die Antwort endet mitten im JSON.
- **iPhone-Home-Bildschirm:** In Safari „Teilen → Zum Home-Bildschirm“ legt Tischlein mit eigenem Symbol
  ab. Das Manifest nutzt `display: browser`: eine eigenständige Web-App hätte einen eigenen Cookie-Speicher
  und käme nicht durch die Anmeldung von Cloudflare Access.
- **Offline:** Ein Service Worker (`/sw.js`) hält die Einkaufsliste und die Stildateien vor; ohne Netz
  abgehakte Einträge stehen im Browser in einer Warteschlange und gehen beim nächsten Netz raus.
  Hinter Cloudflare Access wird nur eine echte Antwort (kein Umleiten zur Anmeldung) zwischengespeichert.
