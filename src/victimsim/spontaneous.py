"""Self-triggering loop for "distress"/"weak" behavior modes: the victim
shouts and knocks on its own, on a random timer, without needing to hear a
rescuer first. Runs in a background thread alongside the keyword listener;
shares Responder's cooldown so a spontaneous call and a keyword-triggered
one never overlap or fire back-to-back.
"""

from __future__ import annotations

import random
import threading

from .config import SpontaneousProfile
from .responder import Responder


class SpontaneousCaller(threading.Thread):
    def __init__(self, responder: Responder, profile: SpontaneousProfile):
        super().__init__(daemon=True)
        self.responder = responder
        self.profile = profile
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            wait_s = random.uniform(self.profile.interval_min_seconds, self.profile.interval_max_seconds)
            if self._stop_event.wait(wait_s):
                return
            if self.responder.ready():
                self.responder.respond("spontaneous call")
