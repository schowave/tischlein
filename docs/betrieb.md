# Betrieb

## Release und Auto-Update

- GitHub-Release `vX.Y.Z` → `.github/workflows/release.yaml` → `mise run docker:publish` →
  `ghcr.io/schowave/tischlein:{X.Y.Z,X.Y,latest}`. Die Version steckt im Image
  (`TISCHLEIN_VERSION`) und steht in `/healthz` und der Seitenleiste. Vorher die Version in
  `pyproject.toml` anheben.
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
  Gibt jemand eine Woche frei, kommt „<Name> hat die Woche ab 16.11. freigegeben: Mo …“ mit `art: freigabe`.
  **Bring-Abgleich** (Einstellungen → Bring, „Automatisch abgleichen“): Tischlein schickt nach jeder
  Änderung nur das Geänderte als `art: bring_sync` (`offen` [{name, menge}], `erledigt` [name], `liste`);
  `notify_tischlein` im homelab-Repo legt an (`todo.add_item`) bzw. hakt ab (`todo.update_item`).
  Zurück: `tischlein_bring_stand` liest bei jeder Änderung einer Bring-Liste und alle 5 Minuten die
  gewählte Liste (`todo.get_items`) und meldet sie an `POST /api/bring/stand`; Tischlein hakt dort
  Gekauftes ab („abgehakt von Bring“) und übernimmt neue Artikel unter „Aus Bring“. Welche Listen es
  gibt, meldet HA beim Start und stündlich an `POST /api/bring/listen`. Abgleich über den Namen
  (Zwiebel = Zwiebeln); nur die Liste der laufenden Woche.
  Fürs iPhone: Automation mit Webhook-Trigger, Aktion `notify.mobile_app_<gerät>` mit
  `message: "{{ trigger.json.nachricht }}"` (Companion-App). Wer den Eintrag selbst gemacht hat,
  lässt sich über `trigger.json.von` ausfiltern.
- **LLM:** OpenAI-kompatibel (z. B. LiteLLM), in den Einstellungen; Tischlein ruft
  `<Basis-URL>/chat/completions` mit `response_format: json_object`.
- **iPhone-Home-Bildschirm:** In Safari „Teilen → Zum Home-Bildschirm“ legt Tischlein mit eigenem Symbol
  ab. Das Manifest nutzt `display: browser`: eine eigenständige Web-App hätte einen eigenen Cookie-Speicher
  und käme nicht durch die Anmeldung von Cloudflare Access.
- **Offline:** Ein Service Worker (`/sw.js`) hält die Einkaufsliste und die Stildateien vor; ohne Netz
  abgehakte Einträge stehen im Browser in einer Warteschlange und gehen beim nächsten Netz raus.
  Hinter Cloudflare Access wird nur eine echte Antwort (kein Umleiten zur Anmeldung) zwischengespeichert.
