"""German public holidays (bundesweit plus Landesfeiertage), computed without a library."""

from __future__ import annotations

from datetime import date, timedelta

# State codes as in the ISO 3166-2:DE suffix
STATE_HOLIDAYS = {
    "dreikoenige": {"BW", "BY", "ST"},
    "fronleichnam": {"BW", "BY", "HE", "NW", "RP", "SL"},
    "mariae_himmelfahrt": {"SL"},
    "allerheiligen": {"BW", "BY", "NW", "RP", "SL"},
    "reformationstag": {"BB", "HB", "HH", "MV", "NI", "SN", "ST", "SH", "TH"},
    "buss_und_bettag": {"SN"},
    "frauentag": {"BE", "MV"},
    "weltkindertag": {"TH"},
}


def easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


def holidays(year: int, state: str = "") -> dict[date, str]:
    """All public holidays of a year: nationwide plus those of `state` (e.g. "BW")."""
    e = easter(year)
    days = {
        date(year, 1, 1): "Neujahr",
        e - timedelta(days=2): "Karfreitag",
        e + timedelta(days=1): "Ostermontag",
        date(year, 5, 1): "Tag der Arbeit",
        e + timedelta(days=39): "Christi Himmelfahrt",
        e + timedelta(days=50): "Pfingstmontag",
        date(year, 10, 3): "Tag der Deutschen Einheit",
        date(year, 12, 25): "1. Weihnachtstag",
        date(year, 12, 26): "2. Weihnachtstag",
    }
    state = state.upper()
    regional = {
        "dreikoenige": (date(year, 1, 6), "Heilige Drei Könige"),
        "fronleichnam": (e + timedelta(days=60), "Fronleichnam"),
        "mariae_himmelfahrt": (date(year, 8, 15), "Mariä Himmelfahrt"),
        "allerheiligen": (date(year, 11, 1), "Allerheiligen"),
        "reformationstag": (date(year, 10, 31), "Reformationstag"),
        "frauentag": (date(year, 3, 8), "Internationaler Frauentag"),
        "weltkindertag": (date(year, 9, 20), "Weltkindertag"),
    }
    for key, (day, name) in regional.items():
        if state in STATE_HOLIDAYS[key]:
            days[day] = name
    if state in STATE_HOLIDAYS["buss_und_bettag"]:
        nov23 = date(year, 11, 23)
        days[nov23 - timedelta(days=(nov23.weekday() - 2) % 7 or 7)] = "Buß- und Bettag"
    return days


def holidays_between(start: date, end: date, state: str = "") -> dict[date, str]:
    out: dict[date, str] = {}
    for year in range(start.year, end.year + 1):
        out.update({d: n for d, n in holidays(year, state).items() if start <= d <= end})
    return out
