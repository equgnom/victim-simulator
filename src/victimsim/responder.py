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
    strength (categories, volume multiplier, knock probability) for *all*
    responses, keyword-triggered or spontaneous — a weak victim sounds weak
    either way. Voice and knock volume are independent (config.volume.voice
    / .knock). "Knocking mode" (config.knock.loop_enabled) loops the knock
    clip and guarantees a knock on every response, overriding probability.
    """

    def __init__(
        self,
        config: Config,
        sound_bank: SoundBank,
        output_device: int | None,
        state=None,  # victimsim.state.SharedState, optional (web dashboard log/status)
    ):
        self.config = config
        self.sound_bank = sound_bank
        self.output_device = output_device
        self.state = state
        self._last_response_at = 0.0
        self.busy = threading.Event()  # set while playing back; listener mutes on this

    def ready(self) -> bool:
        return (time.monotonic() - self._last_response_at) >= self.config.trigger.cooldown_seconds

    def _strength_params(self) -> tuple[list[str], float, float, float]:
        """Returns (response_categories, knock_probability, voice_volume, knock_volume)."""
        profile = self.config.behavior.active_profile()
        if profile is None:
            categories = self.config.trigger.response_categories
            knock_probability = self.config.knock.probability
            multiplier = 1.0
        else:
            categories = profile.response_categories
            knock_probability = profile.knock_probability
            multiplier = profile.volume_multiplier

        if not self.config.knock.enabled:
            knock_probability = 0.0
        elif self.config.knock.loop_enabled:
            # "Knocking mode" is a deliberate override: guarantee a knock.
            knock_probability = 1.0

        voice_volume = self.config.volume.voice * multiplier
        knock_volume = self.config.volume.knock * multiplier
        return categories, knock_probability, voice_volume, knock_volume

    def respond(self, reason: str) -> None:
        """`reason` is a short label for the log line, e.g. "heard 'hello'" or
        "spontaneous call"."""
        categories, knock_probability, voice_volume, knock_volume = self._strength_params()

        category = random.choice(categories)
        voice_path = self.sound_bank.pick(category)
        voice = audio_hal.load_clip(voice_path, self.config.audio.playback_sample_rate)

        knock = None
        knock_path = None
        play_knock = random.random() < knock_probability
        if play_knock:
            knock_path = self.sound_bank.pick("knock")
            knock = audio_hal.load_clip(knock_path, self.config.audio.playback_sample_rate)
            if self.config.knock.loop_enabled:
                knock = audio_hal.loop_clip(
                    knock,
                    self.config.knock.loop_count,
                    self.config.knock.loop_gap_seconds,
                    self.config.audio.playback_sample_rate,
                )

        stereo = audio_hal.mix_to_stereo(
            voice,
            knock,
            self.config.audio.voice_channel,
            self.config.audio.knock_channel,
            voice_volume,
            knock_volume,
        )

        log_line = (
            f"[{reason}] -> playing {category}/{voice_path.name}"
            + (
                f" + knock/{knock_path.name}"
                + (f" (looped x{self.config.knock.loop_count})" if self.config.knock.loop_enabled else "")
                if play_knock
                else ""
            )
        )
        print(log_line)
        if self.state is not None:
            self.state.add_log("response", log_line)

        self.busy.set()
        try:
            audio_hal.play_blocking(
                stereo, self.config.audio.playback_sample_rate, self.output_device
            )
        finally:
            self.busy.clear()
            self._last_response_at = time.monotonic()
            if self.state is not None:
                self.state.note_response(reason, category, play_knock)
