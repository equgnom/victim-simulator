"""Detects a physical "three knocks" pattern from a piezo vibration sensor
wired to a GPIO pin (e.g. the DollaTek 5V piezoelectric film vibration
sensor switch module, TTL-level output) — an alternative to speaking a
keyword: a rescuer knocks on the victim's housing and gets the same
dedicated reply.

Uses gpiozero because it ships a MockFactory pin backend, so the pattern
detection and the GPIO wiring can both be exercised by tests without real
hardware — the same "fake instead of the real device" approach used for
audio in audio_hal.py/listener.py.
"""

from __future__ import annotations

import threading
import time

try:
    from gpiozero import Button
except ImportError:  # gpiozero not installed — reported like any other missing device
    Button = None


class KnockSensorError(RuntimeError):
    """The GPIO pin/backend couldn't be set up (library missing, pin busy or
    doesn't exist, no pin factory available on this machine). The caller
    logs it and retries with backoff — it must not kill the app."""


class KnockPatternDetector:
    """Counts knock events in a rolling time window; once `min_knocks` have
    landed within `window_seconds` of each other, calls `on_pattern()` and
    starts counting fresh. Debouncing a single knock's mechanical bounce
    into one event is the sensor listener's job (gpiozero's bounce_time),
    not this class's — this only ever sees already-debounced events.
    """

    def __init__(self, min_knocks: int, window_seconds: float, on_pattern) -> None:
        self.min_knocks = min_knocks
        self.window_seconds = window_seconds
        self.on_pattern = on_pattern
        self._times: list[float] = []
        self._lock = threading.Lock()

    def register_knock(self) -> None:
        now = time.monotonic()
        with self._lock:
            cutoff = now - self.window_seconds
            self._times = [t for t in self._times if t >= cutoff]
            self._times.append(now)
            fire = len(self._times) >= self.min_knocks
            if fire:
                self._times.clear()
        if fire:
            self.on_pattern()


class KnockSensorListener:
    """Owns the GPIO pin for the sensor's lifetime; `close()` releases it.

    The sensor drives its output HIGH on a knock and LOW at rest (a
    comparator module, not a switch to ground), so `pull_up=False` — a
    pulled-down idle line, active on the HIGH pulse.
    """

    def __init__(
        self,
        pin: int,
        debounce_seconds: float,
        min_knocks: int,
        window_seconds: float,
        on_knock_pattern,
    ) -> None:
        if Button is None:
            raise KnockSensorError("gpiozero is not installed — run: pip install gpiozero")

        self._detector = KnockPatternDetector(min_knocks, window_seconds, on_knock_pattern)
        try:
            self._button = Button(pin, pull_up=False, bounce_time=debounce_seconds)
        except Exception as e:  # noqa: BLE001 — gpiozero's own errors vary by backend/pin factory
            raise KnockSensorError(f"could not open GPIO{pin}: {type(e).__name__}: {e}") from e
        self._button.when_pressed = self._detector.register_knock

    def close(self) -> None:
        self._button.close()
