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
import traceback

from .responder import Responder
from .state import SharedState

# How often to re-check the mode while "responsive" (i.e. not self-calling).
IDLE_POLL_SECONDS = 1.0
# After a failed cycle, wait at least this long before the next one. A bad config
# value fails instantly (not after the usual random interval), so without this the
# loop would spin at 100% CPU.
ERROR_RETRY_SECONDS = 5.0


class SpontaneousLoop(threading.Thread):
    def __init__(self, state: SharedState, responder: Responder):
        super().__init__(daemon=True)
        self.state = state
        self.responder = responder
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        """Never ends because of a failure: an unhandled exception here used to kill
        the thread silently (the traceback only reached the console), leaving a
        service that looks healthy but never calls out. Failures are reported to the
        dashboard log and retried instead — the same guarantee the keyword path has.

        Unlike the keyword path, this repeats on a timer, so a persistent problem
        (an emptied sound folder) would recur every cycle: each *distinct* failure
        is logged once, and a note is logged when calls work again.
        """
        last_failure: str | None = None
        while not self._stop_event.is_set():
            try:
                called = self._cycle()
            except Exception as e:  # noqa: BLE001 — anything: a sound problem or a bad config value
                failure = f"{type(e).__name__}: {e}"
                if failure != last_failure:
                    last_failure = failure
                    traceback.print_exc()
                    self.state.add_log("system", f"spontaneous call failed: {failure} — will keep trying")
                self._stop_event.wait(ERROR_RETRY_SECONDS)
            else:
                if called and last_failure is not None:
                    last_failure = None
                    self.state.add_log("system", "spontaneous calls are working again")

    def _cycle(self) -> bool:
        """One idle-poll or wait-then-maybe-call cycle. True if a call was made."""
        profile = self.state.config.behavior.active_profile()
        if profile is None:
            self._stop_event.wait(IDLE_POLL_SECONDS)
            return False

        wait_s = random.uniform(profile.interval_min_seconds, profile.interval_max_seconds)
        if self._stop_event.wait(wait_s):
            return False

        # Mode may have switched back to "responsive" during the wait.
        if self.state.config.behavior.active_profile() is None:
            return False
        if self.responder.ready():
            self.responder.respond("spontaneous call")
            return True
        return False
