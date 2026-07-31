from __future__ import annotations

import random
import threading
import time

from . import audio_hal
from .config import Config
from .sound_bank import SoundBank


class Responder:
    """Owns the react-to-keyword (and, in distress/weak modes, self-triggered)
    logic: cooldown, random clip pick, playback.

    In "responsive" mode, strength comes from the base `trigger`/`knock`
    config. In "distress"/"weak" modes, the active behavior profile governs
    strength (categories, volume, knock probability) for *all* responses,
    keyword-triggered or spontaneous — a weak victim sounds weak either way.
    """

    def __init__(self, config: Config, sound_bank: SoundBank, output_device: int | None):
        self.config = config
        self.sound_bank = sound_bank
        self.output_device = output_device
        self._last_response_at = 0.0
        self.busy = threading.Event()  # set while playing back; listener mutes on this

    def ready(self) -> bool:
        return (time.monotonic() - self._last_response_at) >= self.config.trigger.cooldown_seconds

    def _strength_params(self) -> tuple[list[str], float, float]:
        """Returns (response_categories, knock_probability, volume)."""
        profile = self.config.behavior.active_profile()
        if profile is None:
            categories = self.config.trigger.response_categories
            knock_probability = self.config.knock.probability
            volume = self.config.volume
        else:
            categories = profile.response_categories
            knock_probability = profile.knock_probability
            volume = self.config.volume * profile.volume_multiplier

        if not self.config.knock.enabled:
            knock_probability = 0.0
        return categories, knock_probability, volume

    def respond(self, reason: str) -> None:
        """`reason` is a short label for the log line, e.g. "heard 'hello'" or
        "spontaneous call"."""
        categories, knock_probability, volume = self._strength_params()

        category = random.choice(categories)
        voice_path = self.sound_bank.pick(category)
        voice = audio_hal.load_clip(voice_path, self.config.audio.playback_sample_rate)

        knock = None
        play_knock = random.random() < knock_probability
        if play_knock:
            knock_path = self.sound_bank.knock_clip(self.config.knock.clip)
            knock = audio_hal.load_clip(knock_path, self.config.audio.playback_sample_rate)

        stereo = audio_hal.mix_to_stereo(
            voice,
            knock,
            self.config.audio.voice_channel,
            self.config.audio.knock_channel,
            volume,
        )

        print(
            f"[{reason}] -> playing {category}/{voice_path.name}"
            + (f" + knock/{self.config.knock.clip}" if play_knock else "")
        )

        self.busy.set()
        try:
            audio_hal.play_blocking(
                stereo, self.config.audio.playback_sample_rate, self.output_device
            )
        finally:
            self.busy.clear()
            self._last_response_at = time.monotonic()
