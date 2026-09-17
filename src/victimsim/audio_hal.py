"""Hardware abstraction layer for audio in/out.

Same code path on the dev laptop (default mic/speakers) and on the Pi
(ReSpeaker Lite in, HiFiBerry out) — only `config.yaml` device name
substrings change between the two.
"""

from __future__ import annotations

import numpy as np
import sounddevice as sd
import soundfile as sf

CHANNEL_INDEX = {"left": 0, "right": 1}


def list_devices() -> str:
    return str(sd.query_devices())


def resolve_device(name_substring: str | None, kind: str) -> int | None:
    """Find a device index whose name contains `name_substring` (case-insensitive).

    kind: "input" or "output". Returns None (= system default) if not set
    or not found.
    """
    if not name_substring:
        return None
    devices = sd.query_devices()
    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"
    for idx, dev in enumerate(devices):
        if name_substring.lower() in dev["name"].lower() and dev[channel_key] > 0:
            return idx
    raise RuntimeError(
        f"No {kind} device matching '{name_substring}' found. "
        f"Run `python -m victimsim.main --list-devices` to see available devices."
    )


_clip_cache: dict[tuple[str, int], np.ndarray] = {}


def load_clip(path, target_sr: int) -> np.ndarray:
    """Load a wav file as mono float32, resampled to target_sr if needed.

    Cached by (path, target_sr): decode + resample only happens once per
    clip, so it doesn't add latency to the detection-to-playback path on
    every response — only the first time a given clip is used. Callers
    never mutate the returned array in place (mix_to_stereo/loop_clip both
    build new arrays), so sharing the cached array is safe.
    """
    key = (str(path), target_sr)
    cached = _clip_cache.get(key)
    if cached is not None:
        return cached

    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != target_sr:
        # Simple linear resample — fine for short SFX clips, avoids a scipy dep.
        duration = len(data) / sr
        target_len = int(duration * target_sr)
        x_old = np.linspace(0, duration, num=len(data), endpoint=False)
        x_new = np.linspace(0, duration, num=target_len, endpoint=False)
        data = np.interp(x_new, x_old, data).astype("float32")

    _clip_cache[key] = data
    return data


def preload_all_clips(sound_bank, categories, target_sr: int) -> int:
    """Warms the clip cache for every clip in `categories` (all voice
    categories plus "knock"), so even the *first* response of a session
    doesn't pay disk I/O + resample latency — that already only happens
    once per clip thanks to load_clip's cache, this just moves it to
    startup instead of mid-response. Returns how many clips were loaded."""
    count = 0
    for category in categories:
        for clip_path in sound_bank.clips_in(category):
            load_clip(clip_path, target_sr)
            count += 1
    return count


def loop_clip(clip: np.ndarray, count: int, gap_seconds: float, samplerate: int) -> np.ndarray:
    """Repeats `clip` `count` times back-to-back, with a silent gap between
    repeats, for "knocking mode" (continuous knocking instead of one knock)."""
    if count <= 1:
        return clip
    gap = np.zeros(int(gap_seconds * samplerate), dtype="float32")
    parts = [clip]
    for _ in range(count - 1):
        parts.append(gap)
        parts.append(clip)
    return np.concatenate(parts)


def mix_to_stereo(
    voice: np.ndarray | None,
    knock: np.ndarray | None,
    voice_channel: str,
    knock_channel: str,
    voice_volume: float,
    knock_volume: float,
) -> np.ndarray:
    """Build a stereo buffer with `voice` on voice_channel (at voice_volume)
    and `knock` on knock_channel (at knock_volume, independent of voice_volume),
    mixed if both land on the same channel."""
    length = max(len(voice) if voice is not None else 0, len(knock) if knock is not None else 0)
    stereo = np.zeros((length, 2), dtype="float32")

    if voice is not None:
        ch = CHANNEL_INDEX[voice_channel]
        stereo[: len(voice), ch] += voice * voice_volume
    if knock is not None:
        ch = CHANNEL_INDEX[knock_channel]
        stereo[: len(knock), ch] += knock * knock_volume

    np.clip(stereo, -1.0, 1.0, out=stereo)
    return stereo


def play_blocking(stereo: np.ndarray, samplerate: int, device: int | None) -> None:
    sd.play(stereo, samplerate=samplerate, device=device, latency="low")
    sd.wait()
