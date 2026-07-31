"""Non-interactive smoke test: exercises every module without needing a live
mic conversation (useful in a headless sandbox / CI). Run the real thing with
`python -m victimsim.main` once you're at a machine with a working mic+speakers.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from victimsim import audio_hal
from victimsim.config import Config, MODELS_DIR
from victimsim.sound_bank import SoundBank

MODEL_PATH = MODELS_DIR / "vosk-model-small-en-us-0.15"


def check(label, fn):
    try:
        fn()
        print(f"OK   {label}")
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {label}: {type(e).__name__}: {e}")
        raise


def main():
    config = Config.load()
    check("config loads", lambda: None)

    bank = SoundBank()

    def _load_all_clips():
        for cat in config.trigger.response_categories:
            clip = bank.pick(cat)
            data = audio_hal.load_clip(clip, config.audio.playback_sample_rate)
            assert len(data) > 0, f"{clip} loaded empty"
        knock = audio_hal.load_clip(
            bank.knock_clip(config.knock.clip), config.audio.playback_sample_rate
        )
        assert len(knock) > 0

    check("load all placeholder clips", _load_all_clips)

    def _mix():
        voice = np.zeros(1000, dtype="float32")
        knock = np.zeros(800, dtype="float32")
        stereo = audio_hal.mix_to_stereo(voice, knock, "left", "right", 0.9)
        assert stereo.shape == (1000, 2)

    check("mix_to_stereo shapes", _mix)

    def _devices():
        devices = audio_hal.list_devices()
        assert "in" in devices.lower() or len(devices) > 0

    check("list audio devices", _devices)

    def _vosk_model_loads():
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        if not MODEL_PATH.exists():
            raise FileNotFoundError(MODEL_PATH)
        model = Model(str(MODEL_PATH))
        recognizer = KaldiRecognizer(model, config.audio.mic_sample_rate)
        # Feed a second of silence — just proving the recognizer pipeline runs.
        silence = np.zeros(config.audio.mic_sample_rate, dtype="int16").tobytes()
        recognizer.AcceptWaveform(silence)
        recognizer.FinalResult()

    check("vosk model loads + processes audio", _vosk_model_loads)

    def _playback():
        voice = audio_hal.load_clip(
            SoundBank().pick("shout"), config.audio.playback_sample_rate
        )
        stereo = audio_hal.mix_to_stereo(voice, None, "left", "right", 0.3)
        audio_hal.play_blocking(stereo, config.audio.playback_sample_rate, None)

    check("play a clip through the default output device", _playback)

    print("\nAll smoke checks passed.")


if __name__ == "__main__":
    main()
