import io
import os
import random
from datetime import date

import pytest
from fastapi.testclient import TestClient

from tischlein.app import create_app
from tischlein.config import PlanConfig
from tischlein.service import Tischlein
from tischlein.store import Store


@pytest.fixture
def client(export_bytes):
    svc = Tischlein(Store(":memory:"), PlanConfig(), rng=random.Random(0))
    c = TestClient(create_app(svc))
    r = c.post("/import", files={"datei": ("r.paprikarecipes", io.BytesIO(export_bytes))}, follow_redirects=False)
    assert r.status_code == 303
    return c


def test_full_flow_generate_swap_approve_calendar(client):
    assert client.get("/kalender.ics").text.count("BEGIN:VEVENT") == 0

    plan = client.post("/api/plaene/naechste/erzeugen").json()
    assert plan["status"] == "entwurf"
    assert [m["wochentag"] for m in plan["mahlzeiten"]] == ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]
    assert plan["mahlzeiten"][0]["gericht"] == "Hähnchen-Bowl"
    assert plan["mahlzeiten"][3]["neu"] is True  # Linsen-Dal is the only Neu recipe

    assert client.post("/plaene/naechste/tauschen", data={"tag": plan["mahlzeiten"][0]["datum"]}).status_code == 200
    assert client.post("/plaene/naechste/status", data={"status": "freigegeben"}).status_code == 200
    assert client.get("/api/plan", params={"woche": "naechste"}).json()["status"] == "freigegeben"

    cal = client.get("/kalender.ics")
    assert cal.headers["content-type"].startswith("text/calendar")
    assert cal.text.count("BEGIN:VEVENT") == 5

    page = client.get("/").text
    assert "Hähnchen-Bowl" in page and "Freigegeben, im Kalender" in page


def test_feedback_moves_new_recipe_into_rotation(client):
    client.post("/rueckmeldung", data={"rezept_uid": "C3", "urteil": "ok"})
    plan = client.post("/api/plaene/naechste/erzeugen").json()
    dienstag = plan["mahlzeiten"][1]
    assert dienstag["gericht"] == "Linsen-Dal" and dienstag["hinweis"] == ""


def test_errors_are_reported(client):
    assert client.get("/api/plan", params={"woche": "quatsch"}).status_code == 400
    assert client.post("/plaene/2030-01-07/tauschen", data={"tag": "2030-01-07"}).status_code == 404
    bad = client.post("/import", files={"datei": ("x", io.BytesIO(b"kein zip"))})
    assert bad.status_code == 400


def test_today_endpoint(client):
    body = client.get("/api/heute").json()
    assert body["datum"] == date.today().isoformat()
    assert "gericht" in body


def test_swap_without_alternative_keeps_meal(client):
    plan = client.post("/api/plaene/naechste/erzeugen").json()
    client.post("/plaene/naechste/tauschen", data={"tag": plan["mahlzeiten"][0]["datum"]})
    montag = client.get("/api/plan", params={"woche": "naechste"}).json()["mahlzeiten"][0]
    assert montag["gericht"] == "Hähnchen-Bowl"
    assert "Keine Alternative" in montag["hinweis"]


def test_sync_endpoints(export_bytes):
    from test_paprika_sync import FakePaprika, recipe
    from test_paprika_sync import client as paprika_client

    fake = FakePaprika({"R1": recipe("R1", "h1", ["c-fisch", "c-kids"])})
    svc = Tischlein(Store(":memory:"), PlanConfig(), rng=random.Random(0), paprika=paprika_client(fake))
    c = TestClient(create_app(svc))

    assert c.post("/api/sync").json() == {"ok": True, "ergebnis": "1 Rezepte, 1 geladen, 0 entfernt"}
    assert c.post("/api/sync").json()["ergebnis"] == "1 Rezepte, 0 geladen, 0 entfernt"  # unchanged hash
    plan = c.post("/api/plaene/naechste/erzeugen").json()
    assert plan["mahlzeiten"][2]["gericht"] == "Rezept R1"
    assert "sync_fehler" not in plan
    assert "Jetzt abgleichen" in c.get("/einstellungen").text

    fake.login_status = 401
    svc.paprika._token = None
    assert c.post("/api/sync").status_code == 502
    plan = c.post("/api/plaene/naechste/erzeugen").json()  # still plans with cached recipes
    assert "Anmeldung" in plan["sync_fehler"]
    assert plan["mahlzeiten"][2]["gericht"] == "Rezept R1"


def test_sync_not_configured(client):
    assert client.post("/api/sync").status_code == 502
    assert "Jetzt abgleichen" not in client.get("/").text


def test_week_page_tabs_and_redirects(client):
    assert "Woche planen" in client.get("/?woche=naechste").text
    r = client.post("/plaene/naechste/erzeugen", follow_redirects=False)
    assert r.headers["location"] == "/?woche=naechste"
    page = client.get("/?woche=naechste").text
    assert "Hähnchen-Bowl" in page and "Woche freigeben" in page and "Vorrat pro Thema" not in page
    assert "Vorrat pro Thema" in client.get("/ideen").text  # moved: the week shows only the week
    assert "/rezepte?rezept=A1" in page  # dish links to its recipe for tagging


def test_recipes_page_and_tag_api():
    from test_tagging import WritablePaprika
    from test_tagging import client as paprika_client

    fake = WritablePaprika()
    svc = Tischlein(Store(":memory:"), PlanConfig(), rng=random.Random(0), paprika=paprika_client(fake))
    svc.sync_paprika()
    c = TestClient(create_app(svc))

    page = c.get("/rezepte").text
    assert '"name": "Bowl"' in page and "Rezept oder Zutat suchen" in page and '"zutaten": [' in page

    r = c.post("/api/rezepte/R1/tags", json={"hinzufuegen": ["Kinder-OK"], "entfernen": []})
    assert r.status_code == 200 and r.json()["kinder_ok"] is True
    data = c.get("/api/rezepte").json()
    assert next(x for x in data["recipes"] if x["uid"] == "R1")["kinder_tag"] is True
    assert "Kinder-OK" in data["groups"][0]["tags"]

    assert c.post("/api/rezepte/NOPE/tags", json={"hinzufuegen": ["X"]}).status_code == 404
    svc.paprika = None
    assert c.post("/api/rezepte/R1/tags", json={"hinzufuegen": ["X"]}).status_code == 502


def test_settings_page_and_secret_calendar(client):
    page = client.get("/einstellungen").text
    assert "Verbinden und prüfen" in page and "webcal://" in page
    svc = client.app.state.service
    token = svc.calendar_settings()["token"]
    assert client.get(f"/kalender/{token}.ics").headers["content-type"].startswith("text/calendar")
    assert client.get("/kalender/falsch.ics").status_code == 404

    r = client.post(
        "/einstellungen/kalender", data={"name": "Mittag", "uhrzeit": "12:15", "minuten": 30}, follow_redirects=False
    )
    assert r.status_code == 303
    assert "Mittag" in client.get(f"/kalender/{token}.ics").text
    bad = client.post("/einstellungen/kalender", data={"name": "x", "uhrzeit": "nope", "minuten": 30})
    assert "Uhrzeit bitte" in bad.text

    r = client.post("/einstellungen/kalender/neu", follow_redirects=False)
    assert client.get(f"/kalender/{token}.ics").status_code == 404


def test_settings_paprika_login_error_is_shown(client, monkeypatch):
    from tischlein import service as service_mod

    class Failing:
        def __init__(self, *a, **k):
            pass

        def login(self):
            raise service_mod.PaprikaError("Paprika-Anmeldung fehlgeschlagen: E-Mail oder Passwort falsch")

    monkeypatch.setattr(service_mod, "PaprikaClient", Failing)
    page = client.post("/einstellungen/paprika", data={"email": "a@b.de", "passwort": "x"}).text
    assert "E-Mail oder Passwort falsch" in page and 'value="a@b.de"' in page


def test_sync_redirects_back_to_allowed_page(client):
    assert client.post("/sync", data={"zurueck": "/rezepte"}, follow_redirects=False).headers["location"] == "/rezepte"
    assert (
        client.post("/sync", data={"zurueck": "https://evil.example"}, follow_redirects=False).headers["location"]
        == "/"
    )


def test_sync_start_and_status_endpoints():
    from test_tagging import WritablePaprika
    from test_tagging import client as paprika_client

    svc = Tischlein(Store(":memory:"), PlanConfig(), paprika=paprika_client(WritablePaprika()))
    c = TestClient(create_app(svc))
    assert c.post("/api/sync/start").json()["laeuft"] is True
    svc._sync_thread.join(5)
    assert c.get("/api/sync/status").json()["ergebnis"] == "2 Rezepte, 2 geladen, 0 entfernt"
    svc.paprika = None
    assert c.post("/api/sync/start").status_code == 502


def test_new_pages_render_and_actions_work(client):
    plan = client.post("/api/plaene/naechste/erzeugen").json()
    monday = plan["mahlzeiten"][0]["datum"]
    for path in ["/einkauf?woche=naechste", "/ideen", "/rueckblick", "/einstellungen"]:
        r = client.get(path)
        assert r.status_code == 200, path
    assert "Zutaten sammeln" in client.get("/einkauf?woche=naechste").text
    for name in ["Kaffee", "Milch"]:
        client.post("/api/einkauf/naechste/eintrag", json={"name": name, "menge": ""})
    assert client.post("/api/einkauf/naechste/alle-erledigt", json={"erledigt": True}).json() == {"geaendert": 2}
    assert "Alle wieder offen" in client.get("/einkauf?woche=naechste").text
    removed = client.post("/api/einkauf/naechste/erledigte-entfernen").json()
    assert removed["entfernt"] == 2
    assert client.get("/api/einkauf", params={"woche": "naechste"}).json() == []
    r = client.post("/api/einkauf/naechste/wiederherstellen", json={"eintraege": removed["eintraege"]})
    assert r.json() == {"wiederhergestellt": 2}
    back = client.get("/api/einkauf", params={"woche": "naechste"}).json()
    assert {i["id"] for i in back} == {i["id"] for i in removed["eintraege"]} and all(i["done"] for i in back)

    assert client.post(f"/tage/{monday}/frei", data={"grund": "Urlaub", "woche": "naechste"}).status_code == 200
    page = client.get("/?woche=naechste").text
    assert "Kein Mittagessen" in page and "Urlaub" in page and "Doch kochen" in page
    client.post(f"/tage/{monday}/kochen", data={"woche": "naechste"})
    assert "Doch kochen" not in client.get("/?woche=naechste").text

    uid = client.get("/api/plan", params={"woche": "naechste"}).json()["mahlzeiten"][0]["rezept_uid"]
    r = client.post(
        "/bewertung",
        data={"rezept_uid": uid, "tag": monday, "urteil": "gut"},
        headers={"cf-access-authenticated-user-email": "julia@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert client.app.state.service.store.ratings()[0]["person"] == "julia@example.com"
    assert client.post("/bewertung", data={"rezept_uid": uid, "tag": monday, "urteil": "x"}).status_code == 400

    client.post("/einkauf/naechste/erzeugen")
    items = client.get("/api/einkauf", params={"woche": "naechste"}).json()
    assert items
    assert client.post(f"/api/einkauf/{items[0]['id']}/erledigt", json={"erledigt": True}).json()["ok"]
    new = client.post("/api/einkauf/naechste/eintrag", json={"name": "Kaffee"}).json()["id"]
    assert client.post(f"/api/einkauf/{new}/loeschen").json()["ok"]

    page = client.get("/einstellungen", headers={"cf-access-authenticated-user-email": "julia@example.com"}).text
    assert "julia@example.com" in page
    client.post("/einstellungen/personen", data={"name:julia@example.com": "Julia"})
    assert (
        "Angemeldet als <strong>Julia</strong>"
        in client.get("/", headers={"cf-access-authenticated-user-email": "julia@example.com"}).text
    )

    assert "Kein LLM" in client.post("/ideen/erzeugen").text or client.get("/ideen").status_code == 200
    lists = [{"entity_id": "todo.zuhause", "name": "Zuhause"}, {"entity_id": "todo.ikea", "name": "Ikea"}]
    assert client.post("/api/bring/listen", json={"listen": lists}).json() == {"listen": 2}
    assert "Ikea" in client.get("/einstellungen").text
    assert client.post("/einstellungen/bring", data={"liste": "todo.ikea"}, follow_redirects=False).status_code == 303
    assert client.post("/einstellungen/bring", data={"liste": "todo.x"}).status_code == 400
    assert client.get("/api/bring/ziel").json() == {"liste": "todo.ikea", "abgleich": False}  # no webhook here
    stand = {"liste": "todo.ikea", "eintraege": [{"summary": "Brot", "status": "needs_action"}]}
    assert client.post("/api/bring/stand", json=stand).json() == {"abgehakt": 0, "neu": 0}  # sync off
    assert client.post("/api/ideen/start").status_code == 400
    assert client.get("/api/ideen/status").json()["laeuft"] is False
    assert client.get("/api/morgen").json()["datum"]


def test_week_menu_moves_picks_changes_theme_and_logs_cooked(client):
    plan = client.post("/api/plaene/naechste/erzeugen").json()["mahlzeiten"]
    mon, tue = plan[0]["datum"], plan[1]["datum"]
    page = client.get("/?woche=naechste").text
    assert "Verschieben auf" in page and "Rezept wählen" in page and "Reste morgen" in page

    r = client.post(f"/tage/{mon}/verschieben", data={"nach": tue, "woche": "naechste"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/?woche=naechste&meldung={mon}#tag-{tue}"
    page = client.get(f"/?woche=naechste&meldung={mon}").text
    assert "auf Dienstag verschoben" in page and "Rückgängig" in page
    moved = client.get("/api/plan", params={"woche": "naechste"}).json()["mahlzeiten"]
    assert moved[1]["gericht"] == plan[0]["gericht"] and moved[1]["thema"] == plan[1]["thema"]
    client.post("/plaene/naechste/rueckgaengig")
    undone = client.get("/api/plan", params={"woche": "naechste"}).json()["mahlzeiten"]
    assert undone[0]["gericht"] == plan[0]["gericht"]
    assert client.post("/plaene/naechste/rueckgaengig").status_code == 400  # one step only

    assert "Fischstäbchen" in client.get(f"/tage/{mon}/rezept").text
    client.post(f"/tage/{mon}/rezept", data={"rezept_uid": "B2", "woche": "naechste"})
    assert client.get("/api/plan", params={"woche": "naechste"}).json()["mahlzeiten"][0]["gericht"] == "Fischstäbchen"

    client.post(f"/tage/{tue}/thema", data={"thema": "Fisch", "woche": "naechste"})
    assert client.get("/api/plan", params={"woche": "naechste"}).json()["mahlzeiten"][1]["thema"] == "Fisch"
    assert client.post(f"/tage/{tue}/thema", data={"thema": "Kuchen"}).status_code == 400

    today = date.today().isoformat()
    assert "Was gab es?" in client.get("/nachtrag").text
    r = client.post("/nachtrag", data={"tag": today, "name": "Pizza", "zurueck": "/rueckblick"}, follow_redirects=False)
    assert r.headers["location"] == "/rueckblick"
    assert "Pizza" in client.get("/rueckblick").text
    assert client.post("/nachtrag", data={"tag": "2999-01-01", "name": "Später"}).status_code == 400
    r = client.post("/nachtrag", data={"tag": today, "name": "x", "zurueck": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"].startswith("/?meldung=")  # never off-site: back to the week, with Rückgängig
    assert "Rückgängig" not in client.get("/?meldung=2000-01-03").text  # only the note of the changed week


def test_approved_week_is_not_rolled_again_from_the_page(client):
    client.post("/api/plaene/naechste/erzeugen")
    client.post("/plaene/naechste/status", data={"status": "freigegeben"})
    r = client.post("/plaene/naechste/erzeugen")
    assert r.status_code == 400 and "Wieder bearbeiten" in r.json()["detail"]
    page = client.get("/?woche=naechste").text
    assert "Woche neu planen" not in page and "Wieder bearbeiten" in page


def test_ideas_for_one_theme(client):
    svc = client.app.state.service
    assert client.post("/api/ideen/start", json={"thema": "Fisch"}).status_code == 400  # no LLM yet
    svc.store.set_meta("llm_base_url", "u")
    svc.store.set_meta("llm_api_key", "k")
    svc.store.set_meta("llm_model", "m")
    r = client.post("/api/ideen/start", json={"thema": "Kuchen"})
    assert r.status_code == 400 and "Unbekanntes Thema" in r.json()["detail"]
    assert "Ideen dazu" in client.get("/ideen").text


def test_api_keeps_an_approved_week_for_home_assistant(client):
    first = client.post("/api/plaene/naechste/erzeugen").json()
    client.post("/plaene/naechste/status", data={"status": "freigegeben"})
    again = client.post("/api/plaene/naechste/erzeugen").json()
    assert again["status"] == "freigegeben" and again["mahlzeiten"] == first["mahlzeiten"] and again["hinweis"]
    bad = client.post("/api/einkauf/naechste/wiederherstellen", json={"eintraege": [{"id": "x", "name": "Milch"}]})
    assert bad.status_code == 400


def test_static_files_get_a_new_address_when_their_content_changes(tmp_path, monkeypatch):
    from tischlein import app as webapp

    (tmp_path / "static").mkdir()
    file = tmp_path / "static" / "x.js"
    file.write_text("eins")
    monkeypatch.setattr(webapp, "HERE", tmp_path)
    first = webapp.asset("x.js")
    assert first.startswith("/static/x.js?v=") and webapp.asset("x.js") == first
    file.write_text("zwei")
    os.utime(file, ns=(1, 1))  # also when the clock is too coarse to tell the two writes apart
    assert webapp.asset("x.js") != first
