from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from victimsim import audio_hal
from victimsim.config import Config
from victimsim.responder import Responder
from victimsim.sound_bank import SoundBank
from victimsim.state import SharedState

SR = 100  # tiny "sample rate" so a clip's duration in seconds is easy to reason about


def clip(seconds: float) -> np.ndarray:
    return np.zeros((int(SR * seconds), 2), dtype="float32")


class FakeDevice:
    """Stands in for sounddevice's play()/wait()/get_stream(): playback 'takes' `takes`
    seconds, or never finishes (takes=None). `abort_frees` = whether abort() unblocks a stalled wait,
    as the real stream's finished-callback does."""

    def __init__(self, monkeypatch, takes: float | None, abort_frees: bool = True):
        self.takes, self.abort_frees = takes, abort_frees
        self.finished = threading.Event()
        self.aborted = False
        self.play_error: Exception | None = None
        monkeypatch.setattr(audio_hal.sd, "play", self._play)
        monkeypatch.setattr(audio_hal.sd, "wait", self._wait)
        monkeypatch.setattr(audio_hal.sd, "get_stream", lambda: self)

    def _play(self, *args, **kwargs):
        if self.play_error:
            raise self.play_error

    def _wait(self, *args, **kwargs):
        if self.takes is None:
            self.finished.wait()  # a stalled device: never returns on its own
        else:
            time.sleep(self.takes)

    def abort(self, ignore_errors=True):
        self.aborted = True
        if self.abort_frees:
            self.finished.set()

    def release(self):  # test cleanup, so a deliberately-stuck thread doesn't linger
        self.finished.set()


@pytest.fixture(autouse=True)
def quick_limits(monkeypatch):
    monkeypatch.setattr(audio_hal, "PLAYBACK_GRACE_SECONDS", 0.3)
    monkeypatch.setattr(audio_hal, "ABORT_WAIT_SECONDS", 0.2)


def test_normal_playback_returns_and_does_not_abort(monkeypatch):
    device = FakeDevice(monkeypatch, takes=0.05)
    audio_hal.play_blocking(clip(0.1), SR, None)
    assert not device.aborted


def test_a_stalled_device_is_aborted_and_reported_instead_of_hanging_forever(monkeypatch):
    device = FakeDevice(monkeypatch, takes=None)
    started = time.monotonic()
    with pytest.raises(audio_hal.PlaybackError, match="stalled"):
        audio_hal.play_blocking(clip(0.1), SR, None)
    assert time.monotonic() - started < 2.0, "must give up shortly after clip length + grace"
    assert device.aborted
    time.sleep(0.1)
    assert not [t for t in threading.enumerate() if t.name == "playback-wait"], "waiter thread should have ended"


def test_a_device_that_ignores_the_abort_still_cannot_hang_the_caller(monkeypatch):
    device = FakeDevice(monkeypatch, takes=None, abort_frees=False)
    started = time.monotonic()
    try:
        with pytest.raises(audio_hal.PlaybackError, match="stalled"):
            audio_hal.play_blocking(clip(0.1), SR, None)
        assert time.monotonic() - started < 2.0
    finally:
        device.release()


def test_a_slow_but_healthy_playback_is_not_cut_off(monkeypatch):
    """The limit is clip length + grace, not a fixed number: a 1s clip that takes 1.15s
    (slower than real time, but within the grace) must play through."""
    device = FakeDevice(monkeypatch, takes=1.15)
    audio_hal.play_blocking(clip(1.0), SR, None)
    assert not device.aborted


def test_a_long_clip_gets_proportionally_more_time(monkeypatch):
    """Guards against a fixed timeout: 2s of audio finishing at 2.2s is fine."""
    device = FakeDevice(monkeypatch, takes=2.2)
    audio_hal.play_blocking(clip(2.0), SR, None)
    assert not device.aborted


def test_failing_to_open_the_output_device_is_a_playback_error(monkeypatch):
    device = FakeDevice(monkeypatch, takes=0.0)
    device.play_error = audio_hal.sd.PortAudioError("Invalid device")
    with pytest.raises(audio_hal.PlaybackError, match="could not start"):
        audio_hal.play_blocking(clip(0.1), SR, None)


# ---- what the responder does about it -------------------------------------------------------------

def _wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00\x00" * 160)
    return path


@pytest.fixture
def responder_setup(tmp_path, monkeypatch):
    for category in ("shout", "cry", "moan", "knock"):
        _wav(tmp_path / category / "a.wav")
    config = Config.load()
    config.knock.enabled = False
    state = SharedState(config)
    return config, state, Responder(config, SoundBank(tmp_path), None, state=state)


def test_the_responder_survives_a_playback_failure(responder_setup, monkeypatch):
    config, state, responder = responder_setup

    def broken(*_args, **_kwargs):
        raise audio_hal.PlaybackError("output device unplugged")

    monkeypatch.setattr(audio_hal, "play_blocking", broken)
    responder.respond("heard 'hallo'", reply=True)  # must not raise

    assert not responder.busy.is_set(), "the microphone must be unmuted again, or it would never listen again"
    assert not responder.ready(), "the cooldown still applies, so a broken device isn't hammered"
    assert state.response_count == 0, "a response that never played must not be counted"
    assert any("PLAYBACK FAILED" in e.message and "unplugged" in e.message for e in state.snapshot_log())


def test_the_responder_works_again_once_the_device_is_back(responder_setup, monkeypatch):
    config, state, responder = responder_setup
    outcomes = iter([audio_hal.PlaybackError("stalled"), None])

    def flaky(*_args, **_kwargs):
        outcome = next(outcomes)
        if outcome:
            raise outcome

    monkeypatch.setattr(audio_hal, "play_blocking", flaky)
    responder.respond("first", reply=True)
    responder._last_response_at = 0.0  # skip the cooldown
    responder.respond("second", reply=True)

    assert state.response_count == 1
    assert state.last_response["reason"] == "second"
    assert not responder.busy.is_set()
