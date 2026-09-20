from __future__ import annotations

import pytest

from victimsim.config import Config


@pytest.fixture
def config() -> Config:
    return Config.load()


def test_knock_probability_comes_from_the_active_mode(config: Config):
    config.behavior.mode = "responsive"
    assert config.effective_knock_probability() == config.knock.probability
    for mode in ("distress", "weak"):
        config.behavior.mode = mode
        assert config.effective_knock_probability() == config.behavior.profiles[mode].knock_probability


def test_override_wins_in_every_mode_without_touching_the_configured_values(config: Config):
    configured = {m: p.knock_probability for m, p in config.behavior.profiles.items()}
    base = config.knock.probability
    config.knock.probability_override = 1.0
    for mode in ("responsive", "distress", "weak"):
        config.behavior.mode = mode
        assert config.effective_knock_probability() == 1.0
    # the tuned numbers survive, so clearing the override restores them
    assert {m: p.knock_probability for m, p in config.behavior.profiles.items()} == configured
    assert config.knock.probability == base
    config.knock.probability_override = None
    config.behavior.mode = "weak"
    assert config.effective_knock_probability() == configured["weak"]


def test_knocks_disabled_beats_everything(config: Config):
    config.knock.enabled = False
    config.knock.probability_override = 1.0
    config.knock.loop_enabled = True
    assert config.effective_knock_probability() == 0.0


def test_knocking_mode_guarantees_a_knock_even_over_a_lower_override(config: Config):
    config.knock.loop_enabled = True
    config.knock.probability_override = 0.1
    assert config.effective_knock_probability() == 1.0
