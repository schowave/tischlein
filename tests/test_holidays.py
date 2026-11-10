from datetime import date

from tischlein.holidays import easter, holidays, holidays_between


def test_easter_known_years():
    assert easter(2026) == date(2026, 4, 5)
    assert easter(2027) == date(2027, 3, 28)
    assert easter(2024) == date(2024, 3, 31)


def test_nationwide_and_state_holidays():
    h = holidays(2026)
    assert h[date(2026, 4, 3)] == "Karfreitag"
    assert h[date(2026, 5, 14)] == "Christi Himmelfahrt"
    assert date(2026, 11, 1) not in h
    bw = holidays(2026, "bw")
    assert bw[date(2026, 11, 1)] == "Allerheiligen"
    assert bw[date(2026, 6, 4)] == "Fronleichnam"
    assert bw[date(2026, 1, 6)] == "Heilige Drei Könige"
    assert holidays(2026, "SN")[date(2026, 11, 18)] == "Buß- und Bettag"


def test_holidays_between_spans_years():
    got = holidays_between(date(2026, 12, 24), date(2027, 1, 2))
    assert sorted(got.values()) == ["1. Weihnachtstag", "2. Weihnachtstag", "Neujahr"]
