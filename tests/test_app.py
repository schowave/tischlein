import io
import random
from datetime import date

import pytest
from fastapi.testclient import TestClient

from tischlein.app import create_app
from tischlein.config import PlanConfig
from tischlein.service import Tischlein
from tischlein.store import Store
from conftest import make_export


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
    assert "Hähnchen-Bowl" in page and "freigegeben" in page


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
