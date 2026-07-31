from __future__ import annotations

import random
import threading
import time

from . import audio_hal
from .config import Config
from .sound_bank import SoundBank


class Responder:
    """Owns the react-to-keyword logic: cooldown, random clip pick, playback."""

    def __init__(self, config: Config, sound_bank: SoundBank, output_device: int | None):
        self.config = config
        self.sound_bank = sound_bank
        self.output_device = output_device
        self._last_response_at = 0.0
        self.busy = threading.Event()  # set while playing back; listener mutes on this

    def ready(self) -> bool:
        return (time.monotonic() - self._last_response_at) >= self.config.trigger.cooldown_seconds

    def respond(self, matched_keyword: str, heard_text: str) -> None:
        category = random.choice(self.config.trigger.response_categories)
        voice_path = self.sound_bank.pick(category)
        voice = audio_hal.load_clip(voice_path, self.config.audio.playback_sample_rate)

        knock = None
        knock_cfg = self.config.knock
        play_knock = knock_cfg.enabled and random.random() < knock_cfg.probability
        if play_knock:
            knock_path = self.sound_bank.knock_clip(knock_cfg.clip)
            knock = audio_hal.load_clip(knock_path, self.config.audio.playback_sample_rate)

        stereo = audio_hal.mix_to_stereo(
            voice,
            knock,
            self.config.audio.voice_channel,
            self.config.audio.knock_channel,
            self.config.volume,
        )

        print(
            f"[trigger] heard '{heard_text}' (matched '{matched_keyword}') "
            f"-> playing {category}/{voice_path.name}"
            + (f" + knock/{knock_cfg.clip}" if play_knock else "")
        )

        self.busy.set()
        try:
            audio_hal.play_blocking(
                stereo, self.config.audio.playback_sample_rate, self.output_device
            )
        finally:
            self.busy.clear()
            self._last_response_at = time.monotonic()
