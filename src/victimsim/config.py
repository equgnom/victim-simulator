from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
SOUNDS_DIR = REPO_ROOT / "assets" / "sounds"
MODELS_DIR = REPO_ROOT / "assets" / "models"


@dataclass
class AudioConfig:
    input_device: str | None = None
    output_device: str | None = None
    mic_channels: int = 1     # ReSpeaker Lite may expose 2 channels; averaged down to mono for the recognizer
    mic_sample_rate: int = 16000
    playback_sample_rate: int = 44100
    voice_channel: str = "left"
    knock_channel: str = "right"


@dataclass
class TriggerConfig:
    keywords: list[str] = field(default_factory=list)
    cooldown_seconds: float = 6.0
    response_categories: list[str] = field(default_factory=lambda: ["shout", "cry", "moan"])


@dataclass
class KnockConfig:
    enabled: bool = True
    probability: float = 0.5
    clip: str = "knock1.wav"


@dataclass
class Config:
    audio: AudioConfig
    trigger: TriggerConfig
    knock: KnockConfig
    volume: float = 0.9

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "Config":
        raw = yaml.safe_load(path.read_text())
        return cls(
            audio=AudioConfig(**raw.get("audio", {})),
            trigger=TriggerConfig(**raw.get("trigger", {})),
            knock=KnockConfig(**raw.get("knock", {})),
            volume=raw.get("volume", 0.9),
        )
