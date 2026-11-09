import pytest

from tischlein.config import PlanConfig
from tischlein.paprika_sync import PaprikaClient, PaprikaError
from tischlein.service import Tischlein
from tischlein.store import Store


class FakeClient:
    def __init__(self, email, password, ok=True):
        self._email, self.password, self.ok = email, password, ok

    def login(self):
        if not self.ok:
            raise PaprikaError("Paprika-Anmeldung fehlgeschlagen: E-Mail oder Passwort falsch")


def test_connect_checks_login_then_stores_and_survives_restart():
    store = Store(":memory:")
    svc = Tischlein(store, PlanConfig())
    assert svc.paprika_account() == {"verbunden": False, "email": "", "quelle": None}

    with pytest.raises(PaprikaError):
        svc.connect_paprika("a@b.de", "falsch", client_factory=lambda e, p: FakeClient(e, p, ok=False))
    assert store.meta("paprika_password") in (None, "")

    svc.connect_paprika(" a@b.de ", "geheim", client_factory=FakeClient)
    assert svc.paprika_account() == {"verbunden": True, "email": "a@b.de", "quelle": "einstellungen"}

    again = Tischlein(store, PlanConfig(), paprika=None)
    assert isinstance(again.paprika, PaprikaClient) and again.paprika._email == "a@b.de"

    # empty password keeps the stored one for the same email, but not for a new email
    svc.connect_paprika("a@b.de", "", client_factory=FakeClient)
    assert svc.paprika.password == "geheim"
    with pytest.raises(ValueError):
        svc.connect_paprika("neu@b.de", "", client_factory=FakeClient)

    svc.disconnect_paprika()
    assert svc.paprika is None and svc.paprika_account()["verbunden"] is False


def test_settings_login_wins_over_environment():
    store = Store(":memory:")
    store.set_meta("paprika_email", "ui@b.de")
    store.set_meta("paprika_password", "pw")
    svc = Tischlein(store, PlanConfig(), paprika=PaprikaClient("env@b.de", "x"))
    assert svc.paprika._email == "ui@b.de"


def test_calendar_settings_token_and_ics():
    svc = Tischlein(Store(":memory:"), PlanConfig())
    s = svc.calendar_settings()
    assert s["name"] == "Tischlein Mittagessen" and s["uhrzeit"] == "12:00" and s["minuten"] == 45
    assert len(s["token"]) >= 20 and svc.calendar_settings()["token"] == s["token"]

    svc.save_calendar_settings("Mittag bei uns", "12:30", 30)
    assert "X-WR-CALNAME:Mittag bei uns" in svc.calendar_ics()
    for bad in [("x", "25:00", 30), ("x", "mittag", 30), ("x", "12:00", 5)]:
        with pytest.raises(ValueError):
            svc.save_calendar_settings(*bad)
    assert svc.renew_calendar_token() != s["token"]
