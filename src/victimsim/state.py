"""Thread-safe state shared between the audio loop (listener + spontaneous
caller, running in a background thread) and the web dashboard (running in
the main thread). This is the seam that lets the dashboard change language/
mode/volume live and see status + a recognition log without restarting the
process.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .config import Config

CPU_TEMP_PATH = Path("/sys/class/thermal/thermal_zone0/temp")


def read_cpu_temp() -> float | None:
    """Raspberry Pi SoC temperature in °C, or None where unavailable (e.g. this dev laptop)."""
    try:
        raw = CPU_TEMP_PATH.read_text().strip()
        return round(int(raw) / 1000, 1)
    except (OSError, ValueError):
        return None


@dataclass
class LogEntry:
    ts: float
    kind: str  # "heard" | "response" | "cooldown" | "system"
    message: str


class SharedState:
    def __init__(self, config: Config, log_capacity: int = 300):
        self.lock = threading.RLock()
        self.config = config
        self.language = config.language
        self.started_at = time.time()

        self.input_device: int | None = None
        self.output_device: int | None = None
        self.listener_ready = False
        self.last_heard: dict | None = None
        self.last_response: dict | None = None
        self.heard_count = 0
        self.response_count = 0

        self.log: deque[LogEntry] = deque(maxlen=log_capacity)
        self.stop_event = threading.Event()
        self.reload_event = threading.Event()
        self._cpu_temp_warning_active = False

        self.responder = None  # set by main.py once the Responder is constructed

    def add_log(self, kind: str, message: str) -> None:
        with self.lock:
            self.log.append(LogEntry(time.time(), kind, message))

    def snapshot_log(self, since: float = 0.0) -> list[LogEntry]:
        with self.lock:
            return [e for e in self.log if e.ts > since]

    def note_heard(self, keyword: str, text: str) -> None:
        with self.lock:
            self.last_heard = {"keyword": keyword, "text": text, "ts": time.time()}
            self.heard_count += 1

    def note_response(self, reason: str, category: str, knocked: bool) -> None:
        with self.lock:
            self.last_response = {
                "reason": reason,
                "category": category,
                "knocked": knocked,
                "ts": time.time(),
            }
            self.response_count += 1

    def _check_cpu_temp_warning(self, temp: float | None) -> bool:
        """Edge-triggered: logs once when crossing into/out of the warning
        zone, not on every poll."""
        threshold = self.config.monitoring.cpu_temp_warning_c
        warning = temp is not None and temp >= threshold
        if warning and not self._cpu_temp_warning_active:
            self.add_log(
                "system",
                f"CPU temp {temp}°C reached the warning threshold ({threshold}°C) — "
                f"the Pi throttles starting ~80°C, hard-limits at 85°C",
            )
        elif not warning and self._cpu_temp_warning_active:
            self.add_log("system", f"CPU temp back down to {temp}°C, below warning threshold")
        self._cpu_temp_warning_active = warning
        return warning

    def status(self) -> dict:
        with self.lock:
            cpu_temp_c = read_cpu_temp()
            cpu_temp_warning = self._check_cpu_temp_warning(cpu_temp_c)
            return {
                # The Pi has no battery-backed RTC and, in AP mode, no
                # internet for NTP — its clock can be wrong. The dashboard
                # uses this to compute a display-only offset against the
                # viewing device's own clock (see fmtTs() in the template);
                # it doesn't change the Pi's actual system clock.
                "server_time": time.time(),
                "language": self.language,
                "mode": self.config.behavior.mode,
                "voice_volume": self.config.volume.voice,
                "knock_volume": self.config.volume.knock,
                "knock_loop_enabled": self.config.knock.loop_enabled,
                "cooldown_seconds": self.config.trigger.cooldown_seconds,
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "listener_ready": self.listener_ready,
                "input_device": self.input_device,
                "output_device": self.output_device,
                "last_heard": self.last_heard,
                "last_response": self.last_response,
                "heard_count": self.heard_count,
                "response_count": self.response_count,
                "cpu_temp_c": cpu_temp_c,
                "cpu_temp_warning": cpu_temp_warning,
                "cpu_temp_warning_threshold_c": self.config.monitoring.cpu_temp_warning_c,
                "ap_mode_enabled": self.config.network.ap_mode.enabled,
                "ap_mode_ssid": self.config.network.ap_mode.ssid,
                "voice_categories": {
                    "available": list(self.config.trigger.response_categories),
                    "enabled": list(self.config.trigger.enabled_categories),
                },
            }
