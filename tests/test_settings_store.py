from __future__ import annotations

import json
from pathlib import Path

import pytest

from victimsim import settings_store
from victimsim.config import MODEL_NAMES, Config


@pytest.fixture
def config() -> Config:
    return Config.load()


@pytest.fixture
def models_dir(tmp_path: Path) -> Path:
    """A models dir where only the German model 'is installed'."""
    d = tmp_path / "models"
    (d / MODEL_NAMES["de"]).mkdir(parents=True)
    return d


def test_roundtrip_restores_every_setting(config: Config, tmp_path: Path, models_dir: Path):
    config.language = "de"
    config.behavior.mode = "weak"
    config.volume.voice = 0.25
    config.volume.knock = 0.75
    config.knock.loop_enabled = True
    config.knock.probability_override = 1.0
    config.trigger.cooldown_seconds = 12
    config.trigger.enabled_categories = ["moan"]

    path = tmp_path / "settings.json"
    settings_store.save(path, settings_store.snapshot(config))

    fresh = Config.load()
    fresh.language = "en"  # differs from what was saved, so a no-op apply would fail the asserts
    applied = settings_store.apply(fresh, settings_store.load(path), models_dir=models_dir)

    assert set(applied) == {
        "language", "mode", "voice_volume", "knock_volume",
        "knock_loop_enabled", "knock_probability_override", "cooldown_seconds",
        "enabled_categories",
    }
    assert fresh.language == "de"
    assert fresh.behavior.mode == "weak"
    assert fresh.volume.voice == 0.25
    assert fresh.volume.knock == 0.75
    assert fresh.knock.loop_enabled is True
    assert fresh.knock.probability_override == 1.0
    assert fresh.trigger.cooldown_seconds == 12
    assert fresh.trigger.enabled_categories == ["moan"]


def test_missing_file_means_no_overrides(tmp_path: Path):
    assert settings_store.load(tmp_path / "nope.json") == {}


@pytest.mark.parametrize("content", ["{not json", "", "[1, 2, 3]", '"a string"', "null"])
def test_corrupt_or_wrong_shape_file_is_ignored(tmp_path: Path, content: str):
    path = tmp_path / "settings.json"
    path.write_text(content)
    assert settings_store.load(path) == {}


def test_invalid_entries_are_dropped_individually(config: Config, models_dir: Path):
    before_mode = config.behavior.mode
    before_categories = list(config.trigger.enabled_categories)
    before_language = config.language

    applied = settings_store.apply(
        config,
        {
            "mode": "bogus",                       # not a real mode
            "language": "fr",                      # not a supported language
            "voice_volume": True,                  # bool is not a number
            "knock_volume": "loud",                # not a number
            "knock_loop_enabled": "yes",           # not a bool
            "knock_probability_override": 7,       # out of range
            "cooldown_seconds": 9999,              # out of range
            "enabled_categories": ["nope", 3],     # nothing valid left
            "some_future_key": 1,                  # unknown keys are ignored
        },
        models_dir=models_dir,
    )

    assert applied == []
    assert config.behavior.mode == before_mode
    assert config.language == before_language
    assert config.trigger.enabled_categories == before_categories


def test_one_bad_entry_does_not_block_the_good_ones(config: Config, models_dir: Path):
    applied = settings_store.apply(
        config, {"mode": "bogus", "cooldown_seconds": 8}, models_dir=models_dir
    )
    assert applied == ["cooldown_seconds"]
    assert config.trigger.cooldown_seconds == 8


def test_volume_is_clamped(config: Config, models_dir: Path):
    settings_store.apply(config, {"voice_volume": 7, "knock_volume": -3}, models_dir=models_dir)
    assert config.volume.voice == 1.0
    assert config.volume.knock == 0.0


def test_language_ignored_when_its_model_is_not_installed(config: Config, models_dir: Path):
    config.language = "en"
    applied = settings_store.apply(config, {"language": "it"}, models_dir=models_dir)
    assert "language" not in applied
    assert config.language == "en"  # falls back to config.yaml's, so the listener can still start


def test_enabled_categories_keeps_valid_dedupes_and_preserves_order(config: Config, models_dir: Path):
    settings_store.apply(
        config, {"enabled_categories": ["moan", "bogus", "shout", "moan"]}, models_dir=models_dir
    )
    assert config.trigger.enabled_categories == ["moan", "shout"]


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path: Path):
    path = tmp_path / "sub" / "settings.json"  # parent dir doesn't exist yet
    settings_store.save(path, {"cooldown_seconds": 5})
    settings_store.save(path, {"cooldown_seconds": 6})

    assert json.loads(path.read_text()) == {"cooldown_seconds": 6}
    assert [p.name for p in path.parent.iterdir()] == ["settings.json"]


def test_failed_save_keeps_previous_file_intact(tmp_path: Path):
    path = tmp_path / "settings.json"
    settings_store.save(path, {"cooldown_seconds": 5})

    with pytest.raises(TypeError):
        settings_store.save(path, {"bad": object()})  # not JSON-serializable

    assert json.loads(path.read_text()) == {"cooldown_seconds": 5}
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_knock_override_can_be_cleared_by_null(config: Config, models_dir: Path):
    config.knock.probability_override = 1.0
    applied = settings_store.apply(config, {"knock_probability_override": None}, models_dir=models_dir)
    assert applied == ["knock_probability_override"]
    assert config.knock.probability_override is None


def test_knock_override_rejects_non_numbers_and_out_of_range(config: Config, models_dir: Path):
    for bad in ("1", True, -0.1, 1.5, [1]):
        assert settings_store.apply(config, {"knock_probability_override": bad}, models_dir=models_dir) == []
        assert config.knock.probability_override is None


def test_clear_deletes_the_file_and_tolerates_a_missing_one(tmp_path: Path):
    path = tmp_path / "settings.json"
    settings_store.save(path, {"cooldown_seconds": 5})
    settings_store.clear(path)
    assert not path.exists()
    settings_store.clear(path)  # already gone: must not raise
