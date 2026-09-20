from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from victimsim.config import (
    MODEL_NAMES,
    MODELS_DIR,
    Config,
    download_hint,
    installed_languages,
    language_installed,
)


@pytest.fixture
def models_dir(tmp_path: Path) -> Path:
    """Only German is 'installed' — like a device that ran `git pull` but never downloaded the rest."""
    d = tmp_path / "models"
    (d / MODEL_NAMES["de"]).mkdir(parents=True)
    return d


def test_language_installed_reflects_the_models_directory(models_dir: Path):
    assert language_installed("de", models_dir)
    assert not language_installed("it", models_dir)
    assert not language_installed("klingon", models_dir)  # unknown language: never installed


def test_installed_languages_covers_every_supported_language(models_dir: Path):
    assert installed_languages(models_dir) == {"en": False, "de": True, "it": False}


def test_download_hint_names_the_script_and_the_language():
    assert download_hint("it") == "bash scripts/download_vosk_model.sh it"


def test_every_language_has_keywords_and_a_download_case():
    """Adding a language to MODEL_NAMES but forgetting its keyword list would silently
    fall back to the *English* keywords, i.e. 'selecting it has no effect'."""
    config = Config.load()
    script = (Path(__file__).parents[1] / "scripts" / "download_vosk_model.sh").read_text()
    for lang, model_name in MODEL_NAMES.items():
        assert lang in config.trigger.keywords, f"no trigger.keywords.{lang} in config.yaml"
        assert config.trigger.keywords[lang] != config.trigger.keywords["en"] or lang == "en"
        assert model_name in script, f"scripts/download_vosk_model.sh doesn't know how to fetch '{lang}'"


def _kaldi_missing_words(words: list[str], lang: str, capfd) -> list[str]:
    """Words Vosk says are missing from `lang`'s vocabulary (it logs them to stderr when a
    grammar contains them). ensure_ascii=False matters: escaped 'è'/'ö' look 'missing'."""
    from vosk import KaldiRecognizer, Model, SetLogLevel

    SetLogLevel(0)
    try:
        model = Model(str(MODELS_DIR / MODEL_NAMES[lang]))
        KaldiRecognizer(model, 16000, json.dumps(words + ["[unk]"], ensure_ascii=False))
    finally:
        SetLogLevel(-1)
    return re.findall(r"missing in vocabulary: '([^']*)'", capfd.readouterr().err)


@pytest.mark.parametrize("lang", list(MODEL_NAMES))
def test_every_keyword_word_exists_in_that_languages_vocabulary(lang: str, capfd):
    """A keyword containing a word the model can't output can never trigger."""
    if not language_installed(lang):
        pytest.skip(f"{lang} model not installed here ({download_hint(lang)})")
    config = Config.load()
    words = sorted({w for kw in config.trigger.keywords_for(lang) for w in kw.lower().split()})
    assert _kaldi_missing_words(words, lang, capfd) == []


def test_the_vocabulary_check_can_actually_fail(capfd):
    """Guards the test above against passing vacuously (e.g. stderr not being captured)."""
    if not language_installed("en"):
        pytest.skip("en model not installed here")
    assert _kaldi_missing_words(["hello", "qzxwvkjh"], "en", capfd) == ["qzxwvkjh"]
