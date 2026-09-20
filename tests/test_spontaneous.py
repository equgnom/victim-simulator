from __future__ import annotations

import random
import threading
import time
import wave
from pathlib import Path

import pytest

from victimsim import audio_hal
from victimsim import spontaneous as spontaneous_module
from victimsim.config import Config, SpontaneousProfile
from victimsim.responder import Responder
from victimsim.sound_bank import SoundBank
from victimsim.spontaneous import SpontaneousLoop
from victimsim.state import SharedState


def wait_until(condition, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def _wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00\x00" * 160)


@pytest.fixture
def config() -> Config:
    cfg = Config.load()
    cfg.knock.enabled = False
    cfg.trigger.cooldown_seconds = 0
    cfg.behavior.mode = "distress"
    cfg.behavior.profiles["distress"] = SpontaneousProfile(0.02, 0.02, 1.0, ["shout"], 0.0)
    return cfg


@pytest.fixture
def start(monkeypatch):
    """start(state, responder) -> a running SpontaneousLoop, stopped and joined at teardown."""
    monkeypatch.setattr(spontaneous_module, "ERROR_RETRY_SECONDS", 0.05)  # quick unless a test says otherwise
    started: list[SpontaneousLoop] = []

    def _start(state, responder):
        loop = SpontaneousLoop(state, responder)
        loop.start()
        started.append(loop)
        return loop

    yield _start
    for loop in started:
        loop.stop()
    for loop in started:
        loop.join(timeout=3)


class ScriptedResponder:
    """respond() consumes one outcome per call: an Exception to raise, or None for success."""

    def __init__(self, outcomes=(), then=None):
        self.outcomes, self.then, self.calls = list(outcomes), then, 0

    def ready(self) -> bool:
        return True

    def respond(self, reason: str, reply: bool = False) -> None:
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else self.then
        if outcome is not None:
            raise outcome


def failures(state) -> list[str]:
    return [e.message for e in state.snapshot_log() if "spontaneous call failed" in e.message]


def test_an_emptied_sound_folder_no_longer_silently_ends_the_thread(config, tmp_path, monkeypatch, start):
    """The realistic case, with the real Responder and SoundBank: the profile picks a category whose
    folder has no clips. The thread must survive, say so, and pick up again once files exist."""
    monkeypatch.setattr(audio_hal, "play_blocking", lambda *a, **k: None)
    for category in ("cry", "moan", "knock"):
        _wav(tmp_path / category / "a.wav")  # no shout/ at all
    state = SharedState(config)
    state.responder = Responder(config, SoundBank(tmp_path), None, state=state)
    loop = start(state, state.responder)

    assert wait_until(lambda: failures(state)), "the problem must reach the dashboard log"
    assert loop.is_alive(), "the thread must survive it"
    assert "FileNotFoundError" in failures(state)[0] and "shout" in failures(state)[0]
    assert state.response_count == 0

    _wav(tmp_path / "shout" / "a.wav")  # someone puts the recordings back
    assert wait_until(lambda: state.response_count > 0), "calls resume by themselves"
    assert any("working again" in e.message for e in state.snapshot_log())


def test_a_persistent_failure_is_logged_once_not_every_cycle(config, start):
    state = SharedState(config)
    responder = ScriptedResponder(then=FileNotFoundError("No .wav clips in shout"))
    start(state, responder)
    assert wait_until(lambda: responder.calls >= 6), "it keeps trying, cycle after cycle"
    assert len(failures(state)) == 1


def test_a_different_failure_is_reported_when_it_appears(config, start):
    state = SharedState(config)
    responder = ScriptedResponder(
        outcomes=[FileNotFoundError("no clips"), FileNotFoundError("no clips"), RuntimeError("corrupt wav")],
        then=RuntimeError("corrupt wav"),
    )
    start(state, responder)
    assert wait_until(lambda: len(failures(state)) == 2)
    assert "FileNotFoundError" in failures(state)[0] and "RuntimeError" in failures(state)[1]


def test_no_working_again_message_without_a_failure_and_none_while_it_still_fails(config, start):
    state = SharedState(config)
    healthy = ScriptedResponder(then=None)
    start(state, healthy)
    assert wait_until(lambda: healthy.calls >= 3)
    assert not failures(state)
    assert not any("working again" in e.message for e in state.snapshot_log())


def test_a_bad_config_value_is_survived_and_does_not_spin_the_cpu(config, monkeypatch, start):
    """A non-numeric interval fails *instantly* (unlike a missing sound, which only fails after the
    random wait), so without a mandatory wait after a failure this would loop flat out."""
    monkeypatch.setattr(spontaneous_module, "ERROR_RETRY_SECONDS", 5.0)  # the real default
    config.behavior.profiles["distress"].interval_min_seconds = "soon"  # what a typo in config.yaml gives
    attempts = []
    real_uniform = random.uniform
    monkeypatch.setattr(spontaneous_module.random, "uniform", lambda a, b: (attempts.append(1), real_uniform(a, b))[1])

    state = SharedState(config)
    loop = start(state, ScriptedResponder(then=None))
    assert wait_until(lambda: failures(state))
    time.sleep(0.5)

    assert loop.is_alive()
    assert "TypeError" in failures(state)[0]
    assert len(attempts) <= 2, f"{len(attempts)} attempts in 0.5s: the loop is spinning"


def test_calls_resume_once_the_config_value_is_fixed(config, monkeypatch, start):
    good_min = config.behavior.profiles["distress"].interval_min_seconds
    config.behavior.profiles["distress"].interval_min_seconds = "soon"
    state = SharedState(config)
    responder = ScriptedResponder(then=None)
    start(state, responder)
    assert wait_until(lambda: failures(state))
    assert responder.calls == 0

    config.behavior.profiles["distress"].interval_min_seconds = good_min
    assert wait_until(lambda: responder.calls > 0)
    assert wait_until(lambda: any("working again" in e.message for e in state.snapshot_log()))


def test_stopping_is_prompt_even_while_backing_off_after_a_failure(config, monkeypatch, start):
    monkeypatch.setattr(spontaneous_module, "ERROR_RETRY_SECONDS", 30.0)
    state = SharedState(config)
    loop = start(state, ScriptedResponder(then=RuntimeError("boom")))
    assert wait_until(lambda: failures(state))
    started = time.monotonic()
    loop.stop()
    loop.join(timeout=5)
    assert not loop.is_alive() and time.monotonic() - started < 1.0


def test_responsive_mode_still_makes_no_spontaneous_calls(config, start):
    config.behavior.mode = "responsive"
    state = SharedState(config)
    responder = ScriptedResponder(then=None)
    loop = start(state, responder)
    time.sleep(0.3)
    assert responder.calls == 0 and loop.is_alive()
