"""Self-triggering loop for "distress"/"weak" behavior modes: the victim
shouts and knocks on its own, on a random timer, without needing to hear a
rescuer first. Runs in a background thread alongside the keyword listener;
shares Responder's cooldown so a spontaneous call and a keyword-triggered
one never overlap or fire back-to-back.

Polls `state.config.behavior.active_profile()` live (rather than being
constructed with a fixed profile) so that switching modes from the web
dashboard takes effect on the next tick, without restarting this thread.
"""

from __future__ import annotations

import random
import threading

from .responder import Responder
from .state import SharedState

# How often to re-check the mode while "responsive" (i.e. not self-calling).
IDLE_POLL_SECONDS = 1.0


class SpontaneousLoop(threading.Thread):
    def __init__(self, state: SharedState, responder: Responder):
        super().__init__(daemon=True)
        self.state = state
        self.responder = responder
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            profile = self.state.config.behavior.active_profile()
            if profile is None:
                if self._stop_event.wait(IDLE_POLL_SECONDS):
                    return
                continue

            wait_s = random.uniform(profile.interval_min_seconds, profile.interval_max_seconds)
            if self._stop_event.wait(wait_s):
                return

            # Mode may have switched back to "responsive" during the wait.
            if self.state.config.behavior.active_profile() is None:
                continue
            if self.responder.ready():
                self.responder.respond("spontaneous call")
