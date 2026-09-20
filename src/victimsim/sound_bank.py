from __future__ import annotations

import random
from pathlib import Path

from .config import SOUNDS_DIR


class SoundBank:
    """Picks a random clip per category, avoiding an immediate repeat."""

    def __init__(self, sounds_dir: Path = SOUNDS_DIR):
        self.sounds_dir = sounds_dir
        self._last_pick: dict[str, Path] = {}

    def count(self, category: str) -> int:
        """How many .wav clips `category` has — 0 (not an error) if the folder is
        empty or doesn't exist. Looked up live, so files dropped in while the
        simulator is running are picked up without a restart."""
        return len(list((self.sounds_dir / category).glob("*.wav")))

    def has_clips(self, category: str) -> bool:
        return self.count(category) > 0

    def clips_in(self, category: str) -> list[Path]:
        folder = self.sounds_dir / category
        clips = sorted(folder.glob("*.wav"))
        if not clips:
            raise FileNotFoundError(
                f"No .wav clips in {folder}. Add real recordings, or run "
                f"scripts/generate_placeholder_sounds.py for placeholders."
            )
        return clips

    def pick(self, category: str) -> Path:
        """Random clip from `category` (e.g. "shout", "cry", "moan", "knock"),
        avoiding an immediate repeat of the last pick in that category."""
        clips = self.clips_in(category)
        if len(clips) > 1 and category in self._last_pick:
            clips = [c for c in clips if c != self._last_pick[category]]
        choice = random.choice(clips)
        self._last_pick[category] = choice
        return choice
