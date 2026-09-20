"""Persists dashboard-adjustable settings across restarts.

Changes made from the web dashboard (language, mode, volumes, knocking
mode, cooldown, voice categories) live in memory; without this they'd
revert to config.yaml on every service restart, crash-loop recovery, or
power cycle. They're saved to a separate, gitignored JSON file instead of
being written back into config.yaml, because config.yaml is git-tracked
(rewriting it would conflict with `git pull` on the Pi) and rewriting it
through PyYAML would strip its comments.

On startup the saved values are layered on top of config.yaml. Loading is
deliberately forgiving: a missing, corrupt, or stale file (unknown keys,
out-of-range values, a language whose model isn't installed) must never
stop the simulator from starting, so bad entries are dropped individually
and config.yaml's value stays in effect for them.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from .config import BEHAVIOR_MODES, REPO_ROOT, Config, language_installed

DEFAULT_SETTINGS_PATH = REPO_ROOT / "runtime_settings.json"


def snapshot(config: Config) -> dict:
    return {
        "language": config.language,
        "mode": config.behavior.mode,
        "voice_volume": config.volume.voice,
        "knock_volume": config.volume.knock,
        "knock_loop_enabled": config.knock.loop_enabled,
        "knock_probability_override": config.knock.probability_override,
        "cooldown_seconds": config.trigger.cooldown_seconds,
        "enabled_categories": list(config.trigger.enabled_categories),
    }


def save(path: Path, data: dict) -> None:
    """Atomic + durable: write a temp file next to the target, fsync it, then
    os.replace — so a power cut mid-write leaves the previous good file
    intact instead of a truncated one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def clear(path: Path) -> None:
    """Deletes the saved settings (no-op if there are none)."""
    Path(path).unlink(missing_ok=True)


def load(path: Path) -> dict:
    """Saved settings, or {} if there's no usable file."""
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        print(f"Ignoring unreadable saved settings {path}: {e}", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        print(f"Ignoring saved settings {path}: expected a JSON object", file=sys.stderr)
        return {}
    return data


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def apply(config: Config, data: dict, models_dir: Path | None = None) -> list[str]:
    """Layers saved settings onto `config`; returns the names actually applied.

    Each entry is validated on its own and silently dropped if invalid, so
    one bad value can't take the others down with it.
    """
    applied: list[str] = []

    language = data.get("language")
    if isinstance(language, str) and language_installed(language, models_dir):
        config.language = language
        applied.append("language")

    if data.get("mode") in BEHAVIOR_MODES:
        config.behavior.mode = data["mode"]
        applied.append("mode")

    for key, target in (("voice_volume", "voice"), ("knock_volume", "knock")):
        value = _number(data.get(key))
        if value is not None:
            setattr(config.volume, target, max(0.0, min(1.0, value)))
            applied.append(key)

    if isinstance(data.get("knock_loop_enabled"), bool):
        config.knock.loop_enabled = data["knock_loop_enabled"]
        applied.append("knock_loop_enabled")

    if "knock_probability_override" in data:
        override = data["knock_probability_override"]
        if override is None:
            config.knock.probability_override = None
            applied.append("knock_probability_override")
        else:
            value = _number(override)
            if value is not None and 0 <= value <= 1:
                config.knock.probability_override = value
                applied.append("knock_probability_override")

    cooldown = _number(data.get("cooldown_seconds"))
    if cooldown is not None and 0 <= cooldown <= 300:
        config.trigger.cooldown_seconds = cooldown
        applied.append("cooldown_seconds")

    categories = data.get("enabled_categories")
    if isinstance(categories, list):
        known = config.trigger.response_categories
        strings = (c for c in categories if isinstance(c, str))
        kept = [c for c in dict.fromkeys(strings) if c in known]
        if kept:  # at least one must stay enabled, same rule as the dashboard
            config.trigger.enabled_categories = kept
            applied.append("enabled_categories")

    return applied
