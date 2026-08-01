"""Non-interactive smoke test: exercises every module without needing a live
mic conversation (useful in a headless sandbox / CI). Run the real thing with
`python -m victimsim.main` once you're at a machine with a working mic+speakers.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from victimsim import audio_hal
from victimsim.config import Config, MODELS_DIR, MODEL_NAMES, SpontaneousProfile
from victimsim.responder import Responder
from victimsim.sound_bank import SoundBank
from victimsim.spontaneous import SpontaneousLoop
from victimsim.state import SharedState
from victimsim.web import create_app


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

    def _keywords_per_language():
        for lang in ("en", "de"):
            kws = config.trigger.keywords_for(lang)
            assert kws, f"no keywords configured for '{lang}'"

    check("keyword lists present for en + de", _keywords_per_language)

    def _vosk_models_load():
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        for lang, model_name in MODEL_NAMES.items():
            model_path = MODELS_DIR / model_name
            if not model_path.exists():
                raise FileNotFoundError(f"[{lang}] {model_path}")
            model = Model(str(model_path))
            recognizer = KaldiRecognizer(model, config.audio.mic_sample_rate)
            silence = np.zeros(config.audio.mic_sample_rate, dtype="int16").tobytes()
            recognizer.AcceptWaveform(silence)
            recognizer.FinalResult()

    check("vosk models load + process audio (en + de)", _vosk_models_load)

    def _responder_per_mode():
        for mode in ("responsive", "distress", "weak"):
            config.behavior.mode = mode
            responder = Responder(config, bank, None)
            assert responder.ready()
            responder.respond(f"smoke test ({mode})")
            categories, knock_prob, volume = responder._strength_params()
            assert categories
            assert 0.0 <= knock_prob <= 1.0
            assert volume > 0
        config.behavior.mode = "responsive"

    check("responder.respond() works in responsive/distress/weak modes", _responder_per_mode)

    def _spontaneous_loop_runs():
        config.behavior.mode = "distress"
        config.behavior.profiles["distress"] = SpontaneousProfile(
            interval_min_seconds=0.05,
            interval_max_seconds=0.1,
            volume_multiplier=0.2,
            response_categories=["moan"],
            knock_probability=0.0,
        )
        state = SharedState(config)
        responder = Responder(config, bank, None, state=state)
        state.responder = responder

        loop = SpontaneousLoop(state, responder)
        loop.start()
        time.sleep(0.5)
        loop.stop()
        # stop() only takes effect between calls, so if it fired mid-playback
        # (clips run up to ~2.5s), give it enough headroom to finish + exit.
        loop.join(timeout=5)
        assert not loop.is_alive(), "SpontaneousLoop thread did not stop"
        assert state.response_count > 0, "SpontaneousLoop never triggered a response"
        config.behavior.mode = "responsive"

    check("SpontaneousLoop starts, fires, logs to state, and stops cleanly", _spontaneous_loop_runs)

    def _web_dashboard():
        config.behavior.mode = "responsive"
        state = SharedState(config)
        responder = Responder(config, bank, None, state=state)
        state.responder = responder
        state.listener_ready = True

        app = create_app(state)
        client = app.test_client()

        assert client.get("/").status_code == 200
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/log").status_code == 200

        assert client.post("/api/mode", json={"mode": "bogus"}).status_code == 400
        assert client.post("/api/mode", json={"mode": "distress"}).status_code == 200
        assert state.config.behavior.mode == "distress"

        assert client.post("/api/language", json={"language": "fr"}).status_code == 400
        resp = client.post("/api/language", json={"language": "de"})
        assert resp.status_code == 200
        assert state.language == "de"
        assert state.reload_event.is_set(), "language switch should signal the audio loop to reload"

        assert client.post("/api/volume", json={"volume": 2.0}).status_code == 200
        assert state.config.volume == 1.0, "volume should be clamped to [0, 1]"

        resp = client.post("/api/trigger", json={})
        assert resp.status_code == 200
        assert state.response_count == 1

        resp = client.post("/api/trigger", json={})
        assert resp.status_code == 429, "should refuse a second trigger during cooldown"

        config.behavior.mode = "responsive"
        config.language = "en"

    check("web dashboard: routes, validation, and live state updates", _web_dashboard)

    print("\nAll smoke checks passed.")


if __name__ == "__main__":
    main()
