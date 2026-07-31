from __future__ import annotations

import wave
from pathlib import Path

import pytest

from victimsim.sound_bank import SoundBank


def _make_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00\x00")


@pytest.fixture
def sounds_dir(tmp_path: Path) -> Path:
    for category, names in {
        "shout": ["a.wav", "b.wav"],
        "cry": ["only.wav"],
        "knock": ["knock1.wav"],
    }.items():
        for name in names:
            _make_wav(tmp_path / category / name)
    return tmp_path


def test_clips_in_missing_category_raises(sounds_dir: Path):
    bank = SoundBank(sounds_dir=sounds_dir)
    with pytest.raises(FileNotFoundError):
        bank.clips_in("moan")


def test_clips_in_returns_sorted_wavs(sounds_dir: Path):
    bank = SoundBank(sounds_dir=sounds_dir)
    clips = bank.clips_in("shout")
    assert [c.name for c in clips] == ["a.wav", "b.wav"]


def test_pick_avoids_immediate_repeat(sounds_dir: Path):
    bank = SoundBank(sounds_dir=sounds_dir)
    previous = bank.pick("shout")
    for _ in range(10):
        current = bank.pick("shout")
        assert current != previous
        previous = current


def test_pick_single_clip_category_repeats(sounds_dir: Path):
    bank = SoundBank(sounds_dir=sounds_dir)
    only = bank.clips_in("cry")[0]
    for _ in range(5):
        assert bank.pick("cry") == only


def test_knock_clip_found(sounds_dir: Path):
    bank = SoundBank(sounds_dir=sounds_dir)
    path = bank.knock_clip("knock1.wav")
    assert path == sounds_dir / "knock" / "knock1.wav"


def test_knock_clip_missing_raises(sounds_dir: Path):
    bank = SoundBank(sounds_dir=sounds_dir)
    with pytest.raises(FileNotFoundError):
        bank.knock_clip("nope.wav")
