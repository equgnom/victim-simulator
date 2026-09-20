"""Web dashboard: status/log monitoring + live mode/language/volume control.

Every setting changed here is also saved via state.persist_settings(), so it
survives a restart (see settings_store.py).

Meant to be reached from another device on the same WLAN as the Pi (e.g.
http://<pi-ip>:8080). No authentication — this is a local-network training
tool, not something to expose to the internet; see README for notes if you
need to lock it down further.
"""

from __future__ import annotations

import traceback
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from .config import BEHAVIOR_MODES, MODEL_NAMES, download_hint, language_installed
from .state import SharedState

TEMPLATE_DIR = Path(__file__).parent / "templates"


def create_app(state: SharedState) -> Flask:
    app = Flask(__name__, template_folder=str(TEMPLATE_DIR))

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/status")
    def get_status():
        return jsonify(state.status())

    @app.get("/api/log")
    def get_log():
        try:
            since = float(request.args.get("since", 0))
        except ValueError:
            since = 0.0
        entries = state.snapshot_log(since)
        return jsonify(
            [{"ts": e.ts, "kind": e.kind, "message": e.message} for e in entries]
        )

    @app.post("/api/mode")
    def set_mode():
        data = request.get_json(silent=True) or {}
        mode = data.get("mode")
        if mode not in BEHAVIOR_MODES:
            return jsonify({"error": f"mode must be one of: {', '.join(BEHAVIOR_MODES)}"}), 400
        with state.lock:
            state.config.behavior.mode = mode
        state.persist_settings()
        state.add_log("system", f"mode changed to '{mode}' via web dashboard")
        return jsonify(state.status())

    @app.post("/api/language")
    def set_language():
        data = request.get_json(silent=True) or {}
        language = data.get("language")
        if language not in MODEL_NAMES:
            return jsonify({"error": f"language must be one of: {list(MODEL_NAMES)}"}), 400
        if not language_installed(language):
            # Loud on purpose: the model is gitignored, so it is missing on any
            # device that only ran `git pull` — silently ignoring the choice
            # looks exactly like "selecting the language does nothing".
            hint = download_hint(language)
            state.add_log(
                "system",
                f"language change to '{language}' refused: its Vosk model isn't installed on this device (run: {hint})",
            )
            return jsonify({
                "error": f"The '{language}' language model isn't installed on this device, so the "
                f"language was NOT changed. On the Pi (needs internet, e.g. Ethernet) run: {hint}"
            }), 400
        with state.lock:
            state.language = language
            state.config.language = language
            state.reload_event.set()
        state.persist_settings()
        state.add_log("system", f"language changed to '{language}' via web dashboard, reloading listener")
        return jsonify(state.status())

    @app.post("/api/volume")
    def set_volume():
        """Body: {"voice": 0.0-1.0} and/or {"knock": 0.0-1.0} — either or
        both, so the two sliders can each POST independently."""
        data = request.get_json(silent=True) or {}
        if "voice" not in data and "knock" not in data:
            return jsonify({"error": "provide 'voice' and/or 'knock'"}), 400

        with state.lock:
            if "voice" in data:
                try:
                    state.config.volume.voice = max(0.0, min(1.0, float(data["voice"])))
                except (TypeError, ValueError):
                    return jsonify({"error": "'voice' must be a number between 0 and 1"}), 400
            if "knock" in data:
                try:
                    state.config.volume.knock = max(0.0, min(1.0, float(data["knock"])))
                except (TypeError, ValueError):
                    return jsonify({"error": "'knock' must be a number between 0 and 1"}), 400

        state.persist_settings()
        state.add_log(
            "system",
            f"volume set to voice={state.config.volume.voice:.2f} "
            f"knock={state.config.volume.knock:.2f} via web dashboard",
        )
        return jsonify(state.status())

    @app.post("/api/knock-loop")
    def set_knock_loop():
        """Body: {"enabled": true/false} — "knocking mode": loop the knock
        clip and guarantee a knock on every response."""
        data = request.get_json(silent=True) or {}
        enabled = data.get("enabled")
        if not isinstance(enabled, bool):
            return jsonify({"error": "'enabled' must be true or false"}), 400
        with state.lock:
            state.config.knock.loop_enabled = enabled
        state.persist_settings()
        state.add_log(
            "system",
            f"knocking mode {'enabled' if enabled else 'disabled'} via web dashboard",
        )
        return jsonify(state.status())

    @app.post("/api/knock-probability")
    def set_knock_probability():
        """Body: {"probability": 0.0-1.0} forces the chance a response includes
        a knock (the dashboard's button sends 1); {"probability": null} clears
        the override, back to the configured per-mode values."""
        data = request.get_json(silent=True) or {}
        if "probability" not in data:
            return jsonify({"error": "provide 'probability' (a number 0-1, or null to clear)"}), 400
        value = data["probability"]
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                return jsonify({"error": "'probability' must be a number between 0 and 1, or null"}), 400
            value = float(value)
        with state.lock:
            state.config.knock.probability_override = value
        state.persist_settings()
        state.add_log(
            "system",
            "knock chance override cleared (back to configured values) via web dashboard"
            if value is None
            else f"knock chance forced to {value:.0%} via web dashboard",
        )
        return jsonify(state.status())

    @app.post("/api/reset-settings")
    def reset_settings():
        applied = state.reset_settings()
        state.add_log(
            "system", f"all dashboard settings reset to config.yaml defaults ({len(applied)} settings)"
        )
        return jsonify(state.status())

    @app.post("/api/cooldown")
    def set_cooldown():
        """Body: {"cooldown_seconds": 5} — minimum gap between any two
        responses, keyword-triggered or spontaneous."""
        data = request.get_json(silent=True) or {}
        try:
            cooldown = float(data.get("cooldown_seconds"))
        except (TypeError, ValueError):
            return jsonify({"error": "'cooldown_seconds' must be a number"}), 400
        if not 0 <= cooldown <= 300:
            return jsonify({"error": "'cooldown_seconds' must be between 0 and 300"}), 400
        with state.lock:
            state.config.trigger.cooldown_seconds = cooldown
        state.persist_settings()
        state.add_log("system", f"cooldown set to {cooldown:.0f}s via web dashboard")
        return jsonify(state.status())

    @app.post("/api/voice-categories")
    def set_voice_categories():
        """Body: {"enabled_categories": ["shout", "moan"]} — checked boxes
        on the dashboard. Must be a non-empty subset of the known
        categories (config.trigger.response_categories) — there always has
        to be something left to play."""
        data = request.get_json(silent=True) or {}
        requested = data.get("enabled_categories")
        if not isinstance(requested, list) or not all(isinstance(c, str) for c in requested):
            return jsonify({"error": "'enabled_categories' must be a list of strings"}), 400

        known = set(state.config.trigger.response_categories)
        unknown = [c for c in requested if c not in known]
        if unknown:
            return jsonify({"error": f"unknown categories: {unknown}. Known: {sorted(known)}"}), 400

        deduped = sorted(set(requested), key=requested.index)
        if not deduped:
            return jsonify({"error": "at least one voice category must stay enabled"}), 400

        with state.lock:
            state.config.trigger.enabled_categories = deduped
        state.persist_settings()
        state.add_log(
            "system", f"voice categories set to {deduped} via web dashboard"
        )
        return jsonify(state.status())

    @app.post("/api/trigger")
    def manual_trigger():
        if state.responder is None or not state.responder.ready():
            return jsonify({"error": "still cooling down, try again shortly"}), 429
        try:
            state.responder.respond("manual trigger from web dashboard")
        except Exception as e:  # noqa: BLE001 — e.g. a sound category with no clips
            traceback.print_exc()
            message = f"{type(e).__name__}: {e}"
            state.add_log("system", f"manual trigger failed: {message}")
            # JSON, so the dashboard's banner shows the actual reason instead of "request failed (500)"
            return jsonify({"error": f"the response failed — {message}"}), 500
        return jsonify(state.status())

    return app
