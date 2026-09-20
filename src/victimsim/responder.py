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
            multiplier = 1.0
        else:
            categories = profile.response_categories
            multiplier = profile.volume_multiplier

        knock_probability = self.config.effective_knock_probability()

        # Global per-category on/off (dashboard checkboxes) filters whatever
        # the mode above selected. If that leaves nothing (e.g. "weak" only
        # offers moan/cry and both are unchecked), fall back to whatever's
        # enabled globally rather than crash — enabled_categories itself is
        # never empty (the API layer refuses to let it become so).
        enabled = set(self.config.trigger.enabled_categories)
        categories = [c for c in categories if c in enabled] or [
            c for c in self.config.trigger.response_categories if c in enabled
        ]

        voice_volume = self.config.volume.voice * multiplier
        knock_volume = self.config.volume.knock * multiplier
        return categories, knock_probability, voice_volume, knock_volume

    def _reply_category(self) -> tuple[str, bool] | None:
        """Where a keyword reply's sound comes from: (category, is_placeholder), or
        None if neither the dedicated category nor its stand-in has any clips."""
        trigger = self.config.trigger
        if self.sound_bank.has_clips(trigger.reply_category):
            return trigger.reply_category, False
        if self.sound_bank.has_clips(trigger.reply_fallback_category):
            return trigger.reply_fallback_category, True
        return None

    def reply_info(self) -> dict:
        """For the dashboard: is the dedicated reply set in place yet?"""
        trigger = self.config.trigger
        clips = self.sound_bank.count(trigger.reply_category)
        return {
            "category": trigger.reply_category,
            "clips": clips,
            "placeholder": trigger.reply_fallback_category
            if clips == 0 and self.sound_bank.has_clips(trigger.reply_fallback_category)
            else None,
        }

    def respond(self, reason: str, reply: bool = False) -> None:
        """`reason` is a short label for the log line, e.g. "heard 'hello'" or
        "spontaneous call".

        `reply=True` (a keyword was heard) plays the dedicated reply sounds
        instead of a random shout/cry/moan — see TriggerConfig.reply_category.
        Volume and knock chance still follow the current mode, but the
        voice-category checkboxes and the mode's category list don't apply:
        those choose among spontaneous-call sounds.
        """
        categories, knock_probability, voice_volume, knock_volume = self._strength_params()

        placeholder = False
        category = None
        if reply:
            choice = self._reply_category()
            if choice is not None:
                category, placeholder = choice
        if category is None:
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
                f" [stand-in: no clips in assets/sounds/{self.config.trigger.reply_category}/ yet]"
                if placeholder
                else ""
            )
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

        played = False
        self.busy.set()
        try:
            audio_hal.play_blocking(
                stereo, self.config.audio.playback_sample_rate, self.output_device
            )
            played = True
        except audio_hal.PlaybackError as e:
            # A stalled or missing output device must not freeze or kill the
            # simulator: say so loudly, unmute the mic, and keep running.
            message = f"PLAYBACK FAILED: {e}"
            print(message)
            if self.state is not None:
                self.state.add_log("system", message)
        finally:
            self.busy.clear()
            self._last_response_at = time.monotonic()  # cooldown applies even to a failed try
            if self.state is not None and played:
                self.state.note_response(
                    reason, f"{category} (reply stand-in)" if placeholder else category, play_knock
                )
