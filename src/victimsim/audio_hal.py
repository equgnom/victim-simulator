"""Hardware abstraction layer for audio in/out.

Same code path on the dev laptop (default mic/speakers) and on the Pi
(ReSpeaker Lite in, HiFiBerry out) — only `config.yaml` device name
substrings change between the two.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

CHANNEL_INDEX = {"left": 0, "right": 1}


def list_devices() -> str:
    return str(sd.query_devices())


class DeviceNotFoundError(RuntimeError):
    """A configured audio device isn't present (unplugged, not enumerated yet)."""


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
    raise DeviceNotFoundError(
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


@dataclass
class PreloadReport:
    loaded: int = 0
    empty: list[str] = field(default_factory=list)  # categories with no .wav files
    unreadable: list[tuple[Path, str]] = field(default_factory=list)  # (clip, why) that failed to load


def preload_all_clips(sound_bank, categories, target_sr: int) -> PreloadReport:
    """Warms the clip cache for every clip in `categories` (all voice
    categories plus "knock"), so even the *first* response of a session
    doesn't pay disk I/O + resample latency — that already only happens
    once per clip thanks to load_clip's cache, this just moves it to
    startup instead of mid-response.

    Never raises for bad *content*: a category with no clips, or a corrupt
    file, used to abort this (and with it the whole app at startup — under
    systemd a crash loop with the dashboard down). They're recorded in the
    returned report for the caller to warn about, and skipped; using such a
    category later fails at that moment instead, where it's guarded and logged.
    """
    report = PreloadReport()
    for category in categories:
        if not sound_bank.has_clips(category):
            report.empty.append(category)
            continue
        for clip_path in sound_bank.clips_in(category):
            try:
                load_clip(clip_path, target_sr)
            except Exception as e:  # noqa: BLE001 — soundfile raises assorted errors for bad files
                report.unreadable.append((clip_path, f"{type(e).__name__}: {e}"))
            else:
                report.loaded += 1
    return report


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


# PortAudio can't be re-initialized while a stream is playing, so playback and
# refresh_devices() take turns.
audio_lock = threading.Lock()


def refresh_devices(after=None) -> None:
    """Re-scan the audio devices.

    PortAudio only enumerates devices when it initializes, so a USB mic that was
    unplugged and replugged is invisible — or sits at a different index — until it
    is re-initialized; retrying to open the old device would never succeed.
    (sounddevice has no public API for this; `_terminate`/`_initialize` are what
    its own FAQ recommends. Must not be called while an input stream is open —
    the listener has closed its stream by the time it gets here.) Waits for any
    playback in progress to finish first. `after`, if given, runs while the lock is
    still held — no playback can start between the re-scan and re-resolving indices.
    """
    with audio_lock:
        sd._terminate()
        sd._initialize()
        if after is not None:
            after()


# A clip may take this much longer than its own length to finish before playback
# counts as stalled. Generous on purpose: this only has to catch a device that
# never finishes, and must never cut off a slow-but-fine playback.
PLAYBACK_GRACE_SECONDS = 3.0
# After aborting a stalled stream, how long to wait for the waiter to notice.
ABORT_WAIT_SECONDS = 2.0


class PlaybackError(RuntimeError):
    """Audio output failed to start or stalled. The caller should log it and carry
    on — it must not freeze or crash the simulator."""


def play_blocking(stereo: np.ndarray, samplerate: int, device: int | None) -> None:
    """Plays `stereo` and returns when it has finished — but never waits forever.

    sd.wait() has no timeout: if the output device ever stalls (unplugged mid-
    playback, a wedged driver), it blocks for good, the responder never returns,
    the microphone stays muted for the rest of the run, and the process still
    looks alive to systemd so nothing restarts it. So the wait is bounded by the
    clip's own length plus PLAYBACK_GRACE_SECONDS; past that the stream is aborted
    and PlaybackError raised.
    """
    with audio_lock:
        _play_bounded(stereo, samplerate, device)


def _play_bounded(stereo: np.ndarray, samplerate: int, device: int | None) -> None:
    try:
        sd.play(stereo, samplerate=samplerate, device=device, latency="low")
    except sd.PortAudioError as e:
        raise PlaybackError(f"could not start audio playback: {e}") from e

    duration = len(stereo) / samplerate
    limit = duration + PLAYBACK_GRACE_SECONDS
    waiter = threading.Thread(target=sd.wait, name="playback-wait", daemon=True)
    waiter.start()
    waiter.join(limit)
    if waiter.is_alive():
        try:
            # abort(), not stop(): stop() drains pending buffers, which is exactly
            # what a wedged device can't do.
            sd.get_stream().abort(ignore_errors=True)
        except Exception:  # noqa: BLE001 — best effort; the error below is what matters
            pass
        waiter.join(ABORT_WAIT_SECONDS)  # a daemon thread: if it's still stuck it can't block exit
        raise PlaybackError(
            f"audio playback stalled: a {duration:.1f}s clip hadn't finished after {limit:.0f}s "
            f"(output device stuck?) — stream aborted"
        )
