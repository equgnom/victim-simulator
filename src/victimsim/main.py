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
import traceback
from pathlib import Path

from . import audio_hal, settings_store
from .config import Config, DEFAULT_CONFIG_PATH, MODELS_DIR, MODEL_NAMES, download_hint
from .listener import KeywordListener, ListenerError
from .responder import Responder
from .sound_bank import SoundBank
from .spontaneous import SpontaneousLoop
from .state import SharedState

# How long to wait before rechecking whether a missing Vosk model has shown up
# (e.g. someone just ran download_vosk_model.sh in another terminal).
MODEL_MISSING_RETRY_SECONDS = 5


MIC_RETRY_MIN_SECONDS = 1.0   # first retry after a microphone failure...
MIC_RETRY_MAX_SECONDS = 10.0  # ...backing off to this, so a mic that stays gone isn't hammered


def _sleep_unless_interrupted(state: SharedState, seconds: float) -> None:
    """Sleeps, but wakes early on shutdown or a language-reload request."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if state.stop_event.is_set() or state.reload_event.is_set():
            return
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


def audio_loop(state: SharedState) -> None:
    """Owns the listener lifecycle. Rebuilds the listener whenever
    `state.reload_event` is set (language switched from the web dashboard).

    Never dies on a microphone problem: a mic that is missing at startup,
    unplugged mid-session, or that stalls without an error is reported (log +
    dashboard "not ready: <reason>") and retried with backoff — re-scanning the
    audio devices each time, because PortAudio only sees USB devices that were
    present when it initialized — until it comes back.
    """
    warned_missing: set[str] = set()  # log a missing model once, not every retry
    while not state.stop_event.is_set():
        state.reload_event.clear()
        language = state.language
        model_path = MODELS_DIR / MODEL_NAMES[language]

        if not model_path.exists():
            state.listener_ready = False
            state.listener_error = f"language model '{language}' is not installed"
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
            device=None,  # resolved fresh before every attempt, see below
            keywords=keywords,
            channels=state.config.audio.mic_channels,
            block_size=state.config.audio.mic_block_size,
        )
        _listen(state, listener, language)

    state.listener_ready = False
    state.add_log("system", "audio loop stopped")


def _listen(state: SharedState, listener: KeywordListener, language: str) -> None:
    state.listener_ready = False
    outage_since: float | None = None  # None = the mic is fine; else when it went away
    last_reason: str | None = None
    delay = MIC_RETRY_MIN_SECONDS

    def on_ready() -> None:
        # First audio block actually arrived: the mic is really delivering.
        nonlocal outage_since, last_reason, delay
        state.listener_ready = True
        state.listener_error = None
        delay = MIC_RETRY_MIN_SECONDS
        if outage_since is None:
            state.add_log("system", f"listener ready (language={language})")
        else:
            message = f"microphone is back after {time.monotonic() - outage_since:.0f}s — listening again (language={language})"
            print(message)
            state.add_log("system", message)
            outage_since = last_reason = None

    def on_failure(reason: str) -> None:
        nonlocal outage_since, last_reason, delay
        state.listener_ready = False
        state.listener_error = reason
        if outage_since is None:
            outage_since = time.monotonic()
            message = f"MICROPHONE UNAVAILABLE: {reason} — retrying with backoff, re-scanning audio devices each time"
        elif reason != last_reason:
            message = f"microphone still unavailable: {reason}"
        else:
            message = ""  # same problem as last attempt: don't spam the log
        last_reason = reason
        if message:
            print(message)
            state.add_log("system", message)

        _sleep_unless_interrupted(state, delay)
        delay = min(delay * 2, MIC_RETRY_MAX_SECONDS)
        if state.stop_event.is_set() or state.reload_event.is_set():
            return
        try:
            audio_hal.refresh_devices(after=lambda: _refresh_output_index(state))
        except Exception as e:  # noqa: BLE001 — best effort; the next attempt reports what's still wrong
            state.add_log("system", f"could not re-scan audio devices: {e}")

    while not state.stop_event.is_set() and not state.reload_event.is_set():
        try:
            state.input_device = listener.device = audio_hal.resolve_device(
                state.config.audio.input_device, "input"
            )
            result = listener.wait_for_keyword(
                mute_event=state.responder.busy, reload_event=state.reload_event, on_ready=on_ready
            )
        except (ListenerError, audio_hal.DeviceNotFoundError) as e:
            on_failure(str(e))
            continue
        except Exception as e:  # noqa: BLE001 — a bug must not silently end listening either
            traceback.print_exc()
            on_failure(f"unexpected {type(e).__name__}: {e}")
            continue

        if result is None:
            break  # reload requested (or stopping) — rebuild/exit outer loop
        keyword, text = result
        state.note_heard(keyword, text)
        state.add_log("heard", f"heard '{text}' (matched '{keyword}')")
        try:
            if state.responder.ready():
                state.responder.respond(f"heard '{text}' (matched '{keyword}')", reply=True)
            else:
                state.add_log("cooldown", f"heard '{text}' but still cooling down, ignored")
        except Exception as e:  # noqa: BLE001 — e.g. an emptied sound folder: report it, keep listening
            traceback.print_exc()
            state.add_log("system", f"response failed: {type(e).__name__}: {e}")


def _refresh_output_index(state: SharedState) -> None:
    """After PortAudio re-scans, device indices can move: re-resolve the output
    device by name so playback doesn't go to the wrong one."""
    name = state.config.audio.output_device
    if not name:
        return
    try:
        index = audio_hal.resolve_device(name, "output")
    except audio_hal.DeviceNotFoundError:
        return  # keep the old index; a failing playback reports itself
    state.output_device = state.responder.output_device = index


def _preload_sounds(config: Config, sound_bank: SoundBank, state: SharedState) -> audio_hal.PreloadReport:
    """Loads every sound into memory at startup. Missing or unreadable sounds are
    warned about (console/journal + dashboard log) but never stop the app: a
    crash here would put the service in a restart loop with the dashboard down,
    exactly when you need it to see what's wrong."""
    to_preload = [*config.trigger.response_categories, "knock"]
    for extra in (config.trigger.reply_category, config.trigger.reply_fallback_category):
        if extra not in to_preload and sound_bank.has_clips(extra):
            to_preload.append(extra)
    report = audio_hal.preload_all_clips(sound_bank, to_preload, config.audio.playback_sample_rate)

    def warn(message: str) -> None:
        print(f"WARNING: {message}")
        state.add_log("system", f"WARNING: {message}")

    for category in report.empty:
        warn(
            f"no .wav clips in {sound_bank.sounds_dir / category} — starting anyway; anything that "
            f"picks '{category}' will fail (and be logged) until you add some"
        )
    for path, why in report.unreadable:
        warn(f"could not load {path} ({why}) — skipped; if it is picked, that response will fail (and be logged)")
    print(f"Preloaded {report.loaded} sound clips into memory (no disk I/O on the response path).")
    return report


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
    # Only the *output* device must exist to start. A missing microphone must not
    # stop the app: it would crash-loop under systemd and take the dashboard down
    # with it, exactly when you need the dashboard to see what's wrong. The audio
    # loop reports it ("not ready: ...") and keeps retrying instead.
    output_device = audio_hal.resolve_device(config.audio.output_device, "output")

    state = SharedState(config)
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
    _preload_sounds(config, sound_bank, state)

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
        target=audio_loop, args=(state,), daemon=True
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
