"""Entry point: runs the audio loop (keyword listener + spontaneous caller)
in a background thread, and serves the web dashboard in the main thread.

Usage:
    python -m victimsim.main --list-devices     # find device indices/names
    python -m victimsim.main                    # run simulator + web dashboard
    python -m victimsim.main --no-web            # CLI-only, no web dashboard
"""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

from . import audio_hal, settings_store
from .config import Config, DEFAULT_CONFIG_PATH, MODELS_DIR, MODEL_NAMES, download_hint
from .listener import KeywordListener
from .responder import Responder
from .sound_bank import SoundBank
from .spontaneous import SpontaneousLoop
from .state import SharedState

# How long to wait before rechecking whether a missing Vosk model has shown up
# (e.g. someone just ran download_vosk_model.sh in another terminal).
MODEL_MISSING_RETRY_SECONDS = 5


def audio_loop(state: SharedState, input_device, output_device) -> None:
    """Owns the listener lifecycle. Rebuilds the listener whenever
    `state.reload_event` is set (language switched from the web dashboard)."""
    warned_missing: set[str] = set()  # log a missing model once, not every retry
    while not state.stop_event.is_set():
        state.reload_event.clear()
        language = state.language
        model_path = MODELS_DIR / MODEL_NAMES[language]

        if not model_path.exists():
            state.listener_ready = False
            if language not in warned_missing:
                warned_missing.add(language)
                message = (
                    f"NOT LISTENING: Vosk model for '{language}' not found at {model_path} "
                    f"(run: {download_hint(language)}, or pick another language)"
                )
                print(message)
                state.add_log("system", message)
            if state.stop_event.wait(MODEL_MISSING_RETRY_SECONDS):
                return
            continue
        warned_missing.discard(language)

        keywords = state.config.trigger.keywords_for(language)
        listener = KeywordListener(
            model_path=model_path,
            samplerate=state.config.audio.mic_sample_rate,
            device=input_device,
            keywords=keywords,
            channels=state.config.audio.mic_channels,
            block_size=state.config.audio.mic_block_size,
        )
        state.listener_ready = True
        state.add_log("system", f"listener ready (language={language})")

        while not state.stop_event.is_set() and not state.reload_event.is_set():
            result = listener.wait_for_keyword(
                mute_event=state.responder.busy, reload_event=state.reload_event
            )
            if result is None:
                break  # reload requested (or stopping) — rebuild/exit outer loop
            keyword, text = result
            state.note_heard(keyword, text)
            state.add_log("heard", f"heard '{text}' (matched '{keyword}')")
            if state.responder.ready():
                state.responder.respond(f"heard '{text}' (matched '{keyword}')", reply=True)
            else:
                state.add_log("cooldown", f"heard '{text}' but still cooling down, ignored")

    state.listener_ready = False
    state.add_log("system", "audio loop stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Responsive Victim Simulator")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config.yaml")
    parser.add_argument(
        "--settings-file",
        default=str(settings_store.DEFAULT_SETTINGS_PATH),
        help="Where dashboard changes are saved so they survive restarts "
        "(default: runtime_settings.json in the repo root; delete it to go back to config.yaml's values)",
    )
    parser.add_argument("--no-web", action="store_true", help="Disable the web dashboard (CLI-only)")
    parser.add_argument("--host", default=None, help="Web dashboard bind host (default: config.yaml web.host)")
    parser.add_argument("--port", type=int, default=None, help="Web dashboard port (default: config.yaml web.port)")
    args = parser.parse_args()

    if args.list_devices:
        print(audio_hal.list_devices())
        return

    config = Config.load(args.config)
    settings_path = Path(args.settings_file)
    defaults = settings_store.snapshot(config)  # config.yaml's values, for "Reset to defaults"
    saved = settings_store.load(settings_path)
    restored = settings_store.apply(config, saved)
    ignored = [k for k in settings_store.snapshot(config) if k in saved and k not in restored]
    input_device = audio_hal.resolve_device(config.audio.input_device, "input")
    output_device = audio_hal.resolve_device(config.audio.output_device, "output")

    state = SharedState(config)
    state.input_device = input_device
    state.output_device = output_device
    state.settings_path = settings_path
    state.defaults = defaults
    if restored:
        note = f"restored saved dashboard settings from {settings_path.name}: {', '.join(restored)}"
        print(note)
        state.add_log("system", note)
    if ignored:
        note = (
            f"ignored saved setting(s) that are invalid or unavailable on this device: {', '.join(ignored)}"
            + (f" (missing language model? run: {download_hint(str(saved.get('language')))})" if "language" in ignored else "")
        )
        print(note)
        state.add_log("system", note)

    sound_bank = SoundBank()
    to_preload = [*config.trigger.response_categories, "knock"]
    for extra in (config.trigger.reply_category, config.trigger.reply_fallback_category):
        if extra not in to_preload and sound_bank.has_clips(extra):
            to_preload.append(extra)
    preloaded = audio_hal.preload_all_clips(sound_bank, to_preload, config.audio.playback_sample_rate)
    print(f"Preloaded {preloaded} sound clips into memory (no disk I/O on the response path).")

    responder = Responder(config, sound_bank, output_device, state=state)
    state.responder = responder
    reply = responder.reply_info()
    if reply["clips"]:
        print(f"Keyword replies: {reply['clips']} clip(s) in assets/sounds/{reply['category']}/.")
    else:
        print(
            f"Keyword replies: assets/sounds/{reply['category']}/ has no clips yet — "
            f"using '{reply['placeholder']}' as a stand-in until you add some."
        )

    spontaneous_loop = SpontaneousLoop(state, responder)
    spontaneous_loop.start()

    audio_thread = threading.Thread(
        target=audio_loop, args=(state, input_device, output_device), daemon=True
    )
    audio_thread.start()

    print(
        f"Responsive Victim Simulator running (language={config.language}, "
        f"mode={config.behavior.mode})."
    )
    if config.network.ap_mode.enabled:
        print(
            f"network.ap_mode is enabled (ssid={config.network.ap_mode.ssid}) — "
            f"this only takes effect once you've run "
            f"`bash scripts/setup_wifi_ap.sh`, it isn't applied automatically."
        )

    def shutdown():
        state.stop_event.set()
        state.reload_event.set()  # unblocks the listener's read loop promptly
        spontaneous_loop.stop()

    if args.no_web:
        print("Web dashboard disabled (--no-web). Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            shutdown()
        return

    from .web import create_app

    host = args.host or config.web.host
    port = args.port or config.web.port
    app = create_app(state)
    print(f"Web dashboard: http://{host}:{port}  (from another device, use the Pi's WLAN IP)")
    print("Press Ctrl+C to stop.")
    try:
        app.run(host=host, port=port, threaded=True)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        shutdown()


if __name__ == "__main__":
    main()
