from datetime import time

import pytest

from tischlein.config import PlanConfig, parse_config


def test_parses_german_config():
    cfg = parse_config(
        {
            "plan": {"neu_slot": "Mittwoch", "cooldown_wochen": 3, "mittag_uhrzeit": "12:30"},
            "tage": [{"tag": "mi", "thema": "Fisch"}, {"tag": "mo", "thema": "Hähnchen"}],
        }
    )
    assert [(d.weekday, d.theme) for d in cfg.days] == [(0, "Hähnchen"), (2, "Fisch")]
    assert cfg.new_slot == 2
    assert cfg.cooldown_weeks == 3
    assert cfg.lunch_time == time(12, 30)


def test_empty_config_uses_defaults_and_empty_new_slot_disables_it():
    assert parse_config({}) == PlanConfig()
    assert parse_config({"plan": {"neu_slot": ""}}).new_slot is None


def test_rejects_unknown_weekday():
    with pytest.raises(ValueError, match="Wochentag"):
        parse_config({"tage": [{"tag": "xx", "thema": "Fisch"}]})
