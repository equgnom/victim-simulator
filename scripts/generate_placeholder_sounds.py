"""Synthesizes placeholder SFX so the pipeline is testable before real
recordings (actors, foley) exist. These are NOT meant to sound convincing —
swap the files in assets/sounds/<category>/ with real recordings later;
everything else (loading, mixing, playback) stays the same.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100
SOUNDS_DIR = Path(__file__).resolve().parents[1] / "assets" / "sounds"


def envelope(n: int, attack: float, release: float) -> np.ndarray:
    env = np.ones(n)
    a = int(n * attack)
    r = int(n * release)
    if a:
        env[:a] = np.linspace(0, 1, a)
    if r:
        env[-r:] = np.linspace(1, 0, r)
    return env


def shout() -> np.ndarray:
    """Alternating two-tone 'siren' — stands in for a shouted 'Help!'."""
    duration = 1.8
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    freq = np.where((t * 4).astype(int) % 2 == 0, 600, 900)
    sig = 0.6 * np.sin(2 * np.pi * freq * t)
    sig *= envelope(len(t), 0.05, 0.15)
    return sig.astype("float32")


def cry() -> np.ndarray:
    """Wavering pitch with vibrato — stands in for crying."""
    duration = 2.5
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    vibrato = 25 * np.sin(2 * np.pi * 5 * t)
    base_freq = 500 - 150 * (t / duration)  # slow downward glide
    phase = 2 * np.pi * np.cumsum(base_freq + vibrato) / SR
    sig = 0.5 * np.sin(phase)
    sig *= envelope(len(t), 0.1, 0.3)
    return sig.astype("float32")


def moan() -> np.ndarray:
    """Low wavering tone with a bit of noise — stands in for a pained moan."""
    duration = 2.0
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    vibrato = 8 * np.sin(2 * np.pi * 3 * t)
    phase = 2 * np.pi * np.cumsum(180 + vibrato) / SR
    sig = 0.45 * np.sin(phase)
    noise = 0.03 * np.random.randn(len(t))
    sig = sig + noise
    sig *= envelope(len(t), 0.15, 0.35)
    return sig.astype("float32")


def knock() -> np.ndarray:
    """Three short percussive thumps — stands in for the knock transducer."""
    gap = 0.3
    thump_dur = 0.12
    total = gap * 3 + thump_dur
    sig = np.zeros(int(SR * total), dtype="float32")
    t_thump = np.linspace(0, thump_dur, int(SR * thump_dur), endpoint=False)
    thump = np.sin(2 * np.pi * 120 * t_thump) * np.exp(-t_thump * 40)
    for i in range(3):
        start = int(SR * gap * i)
        sig[start : start + len(thump)] += thump
    return (sig * 0.9).astype("float32")


def main() -> None:
    generators = {
        "shout": [("help1.wav", shout)],
        "cry": [("cry1.wav", cry)],
        "moan": [("moan1.wav", moan)],
        "knock": [("knock1.wav", knock)],
    }
    for category, files in generators.items():
        out_dir = SOUNDS_DIR / category
        out_dir.mkdir(parents=True, exist_ok=True)
        for filename, gen_fn in files:
            path = out_dir / filename
            sf.write(str(path), gen_fn(), SR)
            print(f"wrote {path.relative_to(SOUNDS_DIR.parents[1])}")


if __name__ == "__main__":
    main()
