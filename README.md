# Tischlein

Familien-Essensplan fürs Mittagessen: rotiert bewährte, eiweißreiche Rezepte aus Paprika nach
Thementagen, plant pro Woche ein neues Rezept und liefert den Plan als Kalender-Abo.

## Loslegen

1. In Paprika die Kategorien anlegen und Rezepte zuordnen: die Themen aus `tischlein.toml`
   (Hähnchen, Hülsenfrüchte, Fisch, Eier/Quark, Pasta/Reis), dazu `Kinder-OK` und `Neu`.
2. Paprika → Einstellungen → Import/Export → alle Rezepte im Paprika-Format exportieren.
3. Starten und importieren:

   ```bash
   docker compose up -d --build
   ```

   Dann `http://<host>:8080` öffnen und die `.paprikarecipes`-Datei hochladen.
4. „Plan erzeugen“, einzelne Tage tauschen, „Freigeben“.
5. Kalender abonnieren: `http://<host>:8080/kalender.ics` (iPhone: Einstellungen → Kalender →
   Accounts → Kalenderabo hinzufügen).

## Home Assistant

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

Kalender: Integration „Remote Calendar“ mit der ICS-URL. Wöchentlicher Entwurf: Automation
(z. B. Samstag 10:00) ruft `rest_command.tischlein_plan_erzeugen` auf. Mit `HA_WEBHOOK_URL`
meldet Tischlein „Mahlzeit: Plan für die Woche … ist da“.

## Entwicklung

```bash
uv run pytest
uv run tischlein serve
```

Mehr zu Domäne und Regeln in [CLAUDE.md](CLAUDE.md).
