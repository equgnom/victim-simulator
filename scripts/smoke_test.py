"""Non-interactive smoke test: exercises every module without needing a live
mic conversation (useful in a headless sandbox / CI). Run the real thing with
`python -m victimsim.main` once you're at a machine with a working mic+speakers.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import soundfile as sf

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


def make_fast_bank(tmp_dir: Path, samplerate: int) -> SoundBank:
    """A SoundBank over tiny (0.05s) synthetic clips, so checks that only
    care about playback *mechanics* (not content) stay fast regardless of
    how long the real recordings in assets/sounds/ grow to be."""
    short = np.zeros(int(0.05 * samplerate), dtype="float32")
    for category in ("shout", "cry", "moan", "knock"):
        cat_dir = tmp_dir / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        sf.write(str(cat_dir / f"{category}.wav"), short, samplerate)
    return SoundBank(sounds_dir=tmp_dir)


def main():
    config = Config.load()
    check("config loads", lambda: None)

    def _ap_mode_config():
        assert config.network.ap_mode.enabled is False
        assert config.network.ap_mode.ssid
        assert len(config.network.ap_mode.password) >= 8, "WPA2 requires an 8+ char password"
        assert config.network.ap_mode.interface

    check("network.ap_mode config parses with sane defaults", _ap_mode_config)

    real_bank = SoundBank()

    def _load_all_clips():
        for cat in [*config.trigger.response_categories, "knock"]:
            clip = real_bank.pick(cat)
            data = audio_hal.load_clip(clip, config.audio.playback_sample_rate)
            assert len(data) > 0, f"{clip} loaded empty"

    check("load all real sound clips (shout/cry/moan/knock)", _load_all_clips)

    # From here on, checks only care about playback mechanics, not content —
    # use tiny synthetic clips so total runtime doesn't grow with however
    # long the real recordings in assets/sounds/ get over time.
    bank = make_fast_bank(Path(tempfile.mkdtemp()), config.audio.playback_sample_rate)

    def _mix():
        voice = np.zeros(1000, dtype="float32")
        knock = np.zeros(800, dtype="float32")
        stereo = audio_hal.mix_to_stereo(voice, knock, "left", "right", 0.9, 0.3)
        assert stereo.shape == (1000, 2)

    check("mix_to_stereo shapes (independent voice/knock volume)", _mix)

    def _loop_clip():
        clip = np.ones(100, dtype="float32")
        looped = audio_hal.loop_clip(clip, count=3, gap_seconds=0.1, samplerate=1000)
        # 3 repeats of 100 samples + 2 gaps of 100 samples (0.1s @ 1000Hz) = 500
        assert len(looped) == 500, f"expected 500 samples, got {len(looped)}"
        assert audio_hal.loop_clip(clip, count=1, gap_seconds=0.1, samplerate=1000) is clip

    check("loop_clip repeats with gaps", _loop_clip)

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
            categories, knock_prob, voice_vol, knock_vol = responder._strength_params()
            assert categories
            assert 0.0 <= knock_prob <= 1.0
            assert voice_vol > 0
            assert knock_vol > 0
        config.behavior.mode = "responsive"

    check("responder.respond() works in responsive/distress/weak modes", _responder_per_mode)

    def _knocking_mode_loops_and_guarantees_knock():
        config.behavior.mode = "responsive"
        config.knock.loop_enabled = True
        config.knock.probability = 0.0  # would normally never knock — loop mode should override this
        config.knock.loop_count = 3
        config.knock.loop_gap_seconds = 0.05
        state = SharedState(config)
        responder = Responder(config, bank, None, state=state)
        state.responder = responder
        responder.respond("smoke test (knocking mode)")
        assert state.last_response["knocked"], "knocking mode should guarantee a knock despite probability=0"
        config.knock.loop_enabled = False
        config.knock.probability = 0.5

    check("knocking mode loops the knock clip and bypasses probability", _knocking_mode_loops_and_guarantees_knock)

    def _independent_volumes():
        config.volume.voice = 0.2
        config.volume.knock = 0.8
        responder = Responder(config, bank, None)
        _, _, voice_vol, knock_vol = responder._strength_params()
        assert abs(voice_vol - 0.2) < 1e-6
        assert abs(knock_vol - 0.8) < 1e-6
        config.volume.voice = 0.9
        config.volume.knock = 0.9

    check("voice and knock volume are independent", _independent_volumes)

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
        # stop() only takes effect between calls, so give it a little
        # headroom past a single fast-bank clip's playback to finish + exit.
        loop.join(timeout=2)
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
        status_resp = client.get("/api/status")
        assert status_resp.status_code == 200
        assert status_resp.get_json()["ap_mode_enabled"] is False
        assert client.get("/api/log").status_code == 200

        assert client.post("/api/mode", json={"mode": "bogus"}).status_code == 400
        assert client.post("/api/mode", json={"mode": "distress"}).status_code == 200
        assert state.config.behavior.mode == "distress"

        assert client.post("/api/language", json={"language": "fr"}).status_code == 400
        resp = client.post("/api/language", json={"language": "de"})
        assert resp.status_code == 200
        assert state.language == "de"
        assert state.reload_event.is_set(), "language switch should signal the audio loop to reload"

        assert client.post("/api/volume", json={}).status_code == 400
        assert client.post("/api/volume", json={"voice": 2.0}).status_code == 200
        assert state.config.volume.voice == 1.0, "voice volume should be clamped to [0, 1]"
        assert client.post("/api/volume", json={"knock": 0.3}).status_code == 200
        assert state.config.volume.knock == 0.3
        assert state.config.volume.voice == 1.0, "knock-only update shouldn't touch voice volume"

        assert client.post("/api/knock-loop", json={"enabled": "yes"}).status_code == 400
        assert client.post("/api/knock-loop", json={"enabled": True}).status_code == 200
        assert state.config.knock.loop_enabled is True
        client.post("/api/knock-loop", json={"enabled": False})

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
