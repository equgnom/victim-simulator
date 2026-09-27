from __future__ import annotations

import threading
import time

import pytest

from victimsim import knock_sensor as knock_sensor_module
from victimsim import main as main_module
from victimsim.config import Config
from victimsim.knock_sensor import KnockPatternDetector, KnockSensorError, KnockSensorListener
from victimsim.state import SharedState

# =====================================================================================
# Layer 1: KnockPatternDetector — pure logic, no hardware involved
# =====================================================================================


def test_fires_once_min_knocks_land_within_the_window(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(knock_sensor_module.time, "monotonic", lambda: now[0])
    fired = []
    detector = KnockPatternDetector(min_knocks=3, window_seconds=2.0, on_pattern=lambda: fired.append(1))

    detector.register_knock()
    now[0] = 0.5
    detector.register_knock()
    assert fired == [], "only two knocks so far"
    now[0] = 1.0
    detector.register_knock()
    assert fired == [1]


def test_knocks_spread_beyond_the_window_never_accumulate(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(knock_sensor_module.time, "monotonic", lambda: now[0])
    fired = []
    detector = KnockPatternDetector(min_knocks=3, window_seconds=2.0, on_pattern=lambda: fired.append(1))

    detector.register_knock()
    now[0] = 3.0  # well past the window — the first knock has expired
    detector.register_knock()
    now[0] = 3.5
    detector.register_knock()
    assert fired == [], "never 3 knocks within any 2s window"


def test_the_count_resets_after_firing(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(knock_sensor_module.time, "monotonic", lambda: now[0])
    fired = []
    detector = KnockPatternDetector(min_knocks=3, window_seconds=2.0, on_pattern=lambda: fired.append(now[0]))

    for t in (0.0, 0.2, 0.4):
        now[0] = t
        detector.register_knock()
    assert fired == [0.4]

    now[0] = 0.5  # a single stray knock right after firing must not immediately refire
    detector.register_knock()
    assert fired == [0.4]


# =====================================================================================
# Layer 2: KnockSensorListener against real gpiozero, backed by its MockFactory
# (the project's convention is to fake the hardware, not the library — gpiozero
# ships its own mock pin backend for exactly this, so this exercises the real
# Button/bounce_time/pull_up wiring instead of a hand-rolled substitute).
# =====================================================================================


@pytest.fixture
def mock_gpio():
    from gpiozero import Device
    from gpiozero.pins.mock import MockFactory

    Device.pin_factory = MockFactory()
    yield Device.pin_factory
    Device.pin_factory.reset()
    Device.pin_factory = None


def test_three_knocks_on_the_real_pin_trigger_the_pattern(mock_gpio):
    fired = []
    listener = KnockSensorListener(
        pin=27, debounce_seconds=0.0, min_knocks=3, window_seconds=2.0, on_knock_pattern=lambda: fired.append(1)
    )
    pin = mock_gpio.pin(27)
    try:
        for _ in range(3):
            pin.drive_high()
            pin.drive_low()
            time.sleep(0.01)
        assert fired == [1]
    finally:
        listener.close()


def test_two_knocks_are_not_enough(mock_gpio):
    fired = []
    listener = KnockSensorListener(
        pin=27, debounce_seconds=0.0, min_knocks=3, window_seconds=2.0, on_knock_pattern=lambda: fired.append(1)
    )
    pin = mock_gpio.pin(27)
    try:
        for _ in range(2):
            pin.drive_high()
            pin.drive_low()
            time.sleep(0.01)
        assert fired == []
    finally:
        listener.close()


def test_gpiozero_not_installed_raises_knock_sensor_error(monkeypatch):
    monkeypatch.setattr(knock_sensor_module, "Button", None)
    with pytest.raises(KnockSensorError, match="not installed"):
        KnockSensorListener(pin=27, debounce_seconds=0.05, min_knocks=3, window_seconds=2.0, on_knock_pattern=lambda: None)


def test_a_pin_the_backend_refuses_raises_knock_sensor_error(monkeypatch):
    def broken(*_a, **_k):
        raise ValueError("pin 999 is not a valid GPIO pin")

    monkeypatch.setattr(knock_sensor_module, "Button", broken)
    with pytest.raises(KnockSensorError, match="could not open GPIO999"):
        KnockSensorListener(pin=999, debounce_seconds=0.05, min_knocks=3, window_seconds=2.0, on_knock_pattern=lambda: None)


# =====================================================================================
# Layer 3: _knock_sensor_loop supervising the listener (mirrors audio_loop's tests)
# =====================================================================================


class FakeKnockListener:
    """Stands in for knock_sensor.KnockSensorListener inside _knock_sensor_loop. One
    script item per construction attempt: ("error", exc) raises; ("ok",) succeeds and
    hands the on_pattern callback back to the test via env.on_pattern."""

    def __init__(self, env, **kwargs):
        item = env.script.pop(0)
        if item[0] == "error":
            raise item[1]
        self.env = env
        self.closed = False
        env.on_pattern = kwargs["on_knock_pattern"]
        env.listeners.append(self)

    def close(self):
        self.closed = True


class Env:
    pass


@pytest.fixture
def env(monkeypatch):
    e = Env()
    config = Config.load()
    config.knock_sensor.enabled = True
    e.state = SharedState(config)
    e.script, e.listeners = [], []
    e.responded = []

    class FakeResponder:
        def __init__(self):
            self._ready = True

        def ready(self):
            return self._ready

        def respond(self, reason, reply=False):
            e.responded.append((reason, reply))

    e.state.responder = FakeResponder()
    monkeypatch.setattr(main_module.knock_sensor, "KnockSensorListener", lambda **kw: FakeKnockListener(e, **kw))
    monkeypatch.setattr(main_module, "KNOCK_SENSOR_RETRY_MIN_SECONDS", 0.01)
    monkeypatch.setattr(main_module, "KNOCK_SENSOR_RETRY_MAX_SECONDS", 0.02)
    return e


def logs(env: Env) -> list[str]:
    return [entry.message for entry in env.state.snapshot_log()]


def _wait_until(predicate, timeout=2.0, message="condition never became true"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    pytest.fail(message)


def _run_until_ready(env, timeout=2.0):
    thread = threading.Thread(target=main_module._knock_sensor_loop, args=(env.state,), daemon=True)
    thread.start()
    try:
        _wait_until(lambda: env.state.knock_sensor_ready, timeout, "knock sensor never became ready")
    except Exception:
        env.state.stop_event.set()
        raise
    return thread


def test_disabled_by_default_does_nothing():
    config = Config.load()
    assert config.knock_sensor.enabled is False
    state = SharedState(config)
    main_module._knock_sensor_loop(state)  # returns immediately, no listener ever built
    assert state.knock_sensor_ready is False


def test_retries_with_backoff_until_the_pin_becomes_available(env):
    same = KnockSensorError("could not open GPIO27: busy")
    env.script = [("error", same), ("error", same), ("ok",)]
    thread = _run_until_ready(env)
    try:
        assert env.state.knock_sensor_ready is True
        assert env.state.knock_sensor_error is None
        assert len([m for m in logs(env) if "KNOCK SENSOR UNAVAILABLE" in m]) == 1, "the same reason repeating is logged once"
        _wait_until(lambda: any("knock sensor is back" in m for m in logs(env)), message="'back' message never logged")
    finally:
        env.state.stop_event.set()
        thread.join(2.0)
    assert env.listeners[0].closed


def test_a_different_failure_reason_is_logged_separately(env):
    env.script = [
        ("error", KnockSensorError("could not open GPIO27: busy")),
        ("error", KnockSensorError("no pin factory found")),
        ("ok",),
    ]
    thread = _run_until_ready(env)
    try:
        assert len([m for m in logs(env) if "KNOCK SENSOR UNAVAILABLE" in m]) == 2
    finally:
        env.state.stop_event.set()
        thread.join(2.0)


def test_a_detected_pattern_triggers_a_reply(env):
    env.script = [("ok",)]
    thread = _run_until_ready(env)
    try:
        env.on_pattern()
        assert env.responded == [("felt 3 knocks on the vibration sensor", True)]
        assert env.state.knock_sensor_hit_count == 1
        assert env.state.last_knock_sensor_hit is not None
    finally:
        env.state.stop_event.set()
        thread.join(2.0)


def test_a_pattern_during_cooldown_is_ignored_not_queued(env):
    env.script = [("ok",)]
    env.state.responder._ready = False
    thread = _run_until_ready(env)
    try:
        env.on_pattern()
        assert env.responded == []
        assert any("still cooling down" in m for m in logs(env))
        assert env.state.knock_sensor_hit_count == 1, "still counted as felt, even though no reply played"
    finally:
        env.state.stop_event.set()
        thread.join(2.0)


def test_a_pattern_while_muted_via_dashboard_is_not_answered(env):
    env.script = [("ok",)]
    env.state.config.knock_sensor.ignored = True
    thread = _run_until_ready(env)
    try:
        env.on_pattern()
        assert env.responded == []
        assert any("muted via dashboard" in m for m in logs(env))
        assert env.state.knock_sensor_hit_count == 1, "still counted as felt, even though muted"
    finally:
        env.state.stop_event.set()
        thread.join(2.0)


def test_muting_takes_effect_live_without_rebuilding_the_listener(env):
    """The dashboard toggles config.knock_sensor.ignored on the fly — the loop
    must see the change without needing to reopen the GPIO pin."""
    env.script = [("ok",)]
    thread = _run_until_ready(env)
    try:
        env.on_pattern()
        assert env.responded == [("felt 3 knocks on the vibration sensor", True)]

        env.state.config.knock_sensor.ignored = True
        env.on_pattern()
        assert env.responded == [("felt 3 knocks on the vibration sensor", True)], "second pattern was muted"
        assert len(env.listeners) == 1, "no reconnect was needed to apply the mute"
    finally:
        env.state.stop_event.set()
        thread.join(2.0)


def test_a_failing_response_does_not_crash_the_loop(env):
    env.script = [("ok",)]

    def boom(reason, reply=False):
        raise FileNotFoundError("No .wav clips in assets/sounds/reply")

    env.state.responder.respond = boom
    thread = _run_until_ready(env)
    try:
        env.on_pattern()
        assert any("response to knock sensor failed" in m for m in logs(env))
        assert thread.is_alive()
    finally:
        env.state.stop_event.set()
        thread.join(2.0)
