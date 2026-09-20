from __future__ import annotations

import wave
from pathlib import Path

import pytest

from victimsim import audio_hal
from victimsim import main as main_module
from victimsim.config import Config
from victimsim.sound_bank import SoundBank
from victimsim.state import SharedState
from victimsim.web import create_app


def _wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00\x00" * 160)
    return path


def _garbage(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"this is not a wav file")
    return path


SR = 44100


def test_a_category_with_no_clips_is_skipped_and_reported_not_fatal(tmp_path):
    _wav(tmp_path / "cry" / "a.wav")
    (tmp_path / "moan").mkdir()  # exists but empty; shout/ doesn't exist at all
    report = audio_hal.preload_all_clips(SoundBank(tmp_path), ["shout", "cry", "moan"], SR)
    assert report.loaded == 1
    assert report.empty == ["shout", "moan"]
    assert not report.unreadable


def test_a_corrupt_clip_is_skipped_and_the_good_ones_still_load(tmp_path):
    _wav(tmp_path / "cry" / "good1.wav")
    _wav(tmp_path / "cry" / "good2.wav")
    bad = _garbage(tmp_path / "cry" / "bad.wav")
    report = audio_hal.preload_all_clips(SoundBank(tmp_path), ["cry"], SR)
    assert report.loaded == 2
    assert [p for p, _ in report.unreadable] == [bad]
    assert report.unreadable[0][1], "the reason is recorded"


def test_no_sounds_at_all_still_returns_instead_of_raising(tmp_path):
    report = audio_hal.preload_all_clips(SoundBank(tmp_path), ["shout", "cry", "moan", "knock"], SR)
    assert report.loaded == 0
    assert report.empty == ["shout", "cry", "moan", "knock"]


def test_a_healthy_bank_reports_nothing_wrong(tmp_path):
    for category in ("shout", "cry"):
        _wav(tmp_path / category / "a.wav")
    report = audio_hal.preload_all_clips(SoundBank(tmp_path), ["shout", "cry"], SR)
    assert (report.loaded, report.empty, report.unreadable) == (2, [], [])


@pytest.fixture
def config() -> Config:
    return Config.load()


def _warnings(state: SharedState) -> list[str]:
    return [e.message for e in state.snapshot_log() if e.message.startswith("WARNING")]


def test_startup_warns_once_per_empty_category_and_carries_on(config, tmp_path, capsys):
    """The scenario that used to crash the app (and, under systemd, loop it): shout/ has no files."""
    for category in ("cry", "moan", "knock"):
        _wav(tmp_path / category / "a.wav")
    state = SharedState(config)

    report = main_module._preload_sounds(config, SoundBank(tmp_path), state)  # must not raise

    warnings = _warnings(state)
    assert len(warnings) == 1 and "shout" in warnings[0] and "no .wav clips" in warnings[0]
    assert "will fail (and be logged)" in warnings[0], "says what the consequence is"
    assert "WARNING" in capsys.readouterr().out, "and it reaches the console/journal too"
    assert report.loaded == 3


def test_startup_names_an_unreadable_file(config, tmp_path):
    for category in ("shout", "cry", "moan", "knock"):
        _wav(tmp_path / category / "a.wav")
    bad = _garbage(tmp_path / "cry" / "broken.wav")
    state = SharedState(config)
    main_module._preload_sounds(config, SoundBank(tmp_path), state)
    warnings = _warnings(state)
    assert len(warnings) == 1 and str(bad) in warnings[0] and "skipped" in warnings[0]


def test_startup_is_quiet_when_everything_is_fine(config, tmp_path):
    for category in ("shout", "cry", "moan", "knock"):
        _wav(tmp_path / category / "a.wav")
    state = SharedState(config)
    main_module._preload_sounds(config, SoundBank(tmp_path), state)
    assert _warnings(state) == []


def test_startup_survives_an_entirely_empty_sound_directory(config, tmp_path):
    """e.g. a fresh clone where the recordings haven't been copied over yet."""
    state = SharedState(config)
    main_module._preload_sounds(config, SoundBank(tmp_path), state)
    assert len(_warnings(state)) == 4  # shout, cry, moan, knock


# ---- the dashboard's "Trigger now" ----------------------------------------------------------------

class FakeResponder:
    def __init__(self, error: Exception | None = None):
        self.error, self.calls = error, 0

    def ready(self) -> bool:
        return True

    def respond(self, reason: str, reply: bool = False) -> None:
        self.calls += 1
        if self.error:
            raise self.error

    def reply_info(self) -> dict:
        return {"category": "reply", "clips": 0, "placeholder": None}


def test_a_failing_manual_trigger_returns_the_reason_not_an_opaque_500(config):
    state = SharedState(config)
    state.responder = FakeResponder(FileNotFoundError("No .wav clips in /x/shout"))
    resp = create_app(state).test_client().post("/api/trigger", json={})

    assert resp.status_code == 500
    assert "FileNotFoundError" in resp.get_json()["error"] and "shout" in resp.get_json()["error"], \
        "JSON with the reason, so the dashboard banner can show it"
    assert any("manual trigger failed" in e.message for e in state.snapshot_log())


def test_a_working_manual_trigger_is_unchanged(config):
    state = SharedState(config)
    state.responder = FakeResponder()
    resp = create_app(state).test_client().post("/api/trigger", json={})
    assert resp.status_code == 200 and state.responder.calls == 1
