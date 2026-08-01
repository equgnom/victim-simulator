"""Web dashboard: status/log monitoring + live mode/language/volume control.

Meant to be reached from another device on the same WLAN as the Pi (e.g.
http://<pi-ip>:8080). No authentication — this is a local-network training
tool, not something to expose to the internet; see README for notes if you
need to lock it down further.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, render_template, request

from .config import MODEL_NAMES, MODELS_DIR
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
        if mode not in ("responsive", "distress", "weak"):
            return jsonify({"error": "mode must be one of: responsive, distress, weak"}), 400
        with state.lock:
            state.config.behavior.mode = mode
        state.add_log("system", f"mode changed to '{mode}' via web dashboard")
        return jsonify(state.status())

    @app.post("/api/language")
    def set_language():
        data = request.get_json(silent=True) or {}
        language = data.get("language")
        if language not in MODEL_NAMES:
            return jsonify({"error": f"language must be one of: {list(MODEL_NAMES)}"}), 400
        model_path = MODELS_DIR / MODEL_NAMES[language]
        if not model_path.exists():
            return jsonify(
                {"error": f"Vosk model for '{language}' not downloaded on this device"}
            ), 400
        with state.lock:
            state.language = language
            state.config.language = language
            state.reload_event.set()
        state.add_log("system", f"language changed to '{language}' via web dashboard, reloading listener")
        return jsonify(state.status())

    @app.post("/api/volume")
    def set_volume():
        data = request.get_json(silent=True) or {}
        try:
            volume = float(data.get("volume"))
        except (TypeError, ValueError):
            return jsonify({"error": "volume must be a number between 0 and 1"}), 400
        volume = max(0.0, min(1.0, volume))
        with state.lock:
            state.config.volume = volume
        state.add_log("system", f"volume set to {volume:.2f} via web dashboard")
        return jsonify(state.status())

    @app.post("/api/trigger")
    def manual_trigger():
        if state.responder is None or not state.responder.ready():
            return jsonify({"error": "still cooling down, try again shortly"}), 429
        state.responder.respond("manual trigger from web dashboard")
        return jsonify(state.status())

    return app
