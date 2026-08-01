from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
SOUNDS_DIR = REPO_ROOT / "assets" / "sounds"
MODELS_DIR = REPO_ROOT / "assets" / "models"

# Vosk model directory name per language — must match what
# scripts/download_vosk_model.sh fetches into assets/models/.
MODEL_NAMES = {
    "en": "vosk-model-small-en-us-0.15",
    "de": "vosk-model-small-de-0.15",
}


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
    keywords: dict[str, list[str]] = field(default_factory=dict)
    cooldown_seconds: float = 6.0
    response_categories: list[str] = field(default_factory=lambda: ["shout", "cry", "moan"])

    def keywords_for(self, language: str) -> list[str]:
        return self.keywords.get(language, self.keywords.get("en", []))


@dataclass
class SpontaneousProfile:
    """Parameters for a self-triggering "victim calls out on its own" mode."""

    interval_min_seconds: float = 20.0
    interval_max_seconds: float = 60.0
    volume_multiplier: float = 1.0
    response_categories: list[str] = field(default_factory=lambda: ["shout", "cry", "moan"])
    knock_probability: float = 0.5


@dataclass
class BehaviorConfig:
    mode: str = "responsive"  # responsive | distress | weak
    profiles: dict[str, SpontaneousProfile] = field(default_factory=dict)

    def active_profile(self) -> SpontaneousProfile | None:
        """None means "responsive": no self-triggering, use base trigger/knock config."""
        if self.mode == "responsive":
            return None
        return self.profiles.get(self.mode, SpontaneousProfile())


@dataclass
class KnockConfig:
    enabled: bool = True
    probability: float = 0.5
    clip: str = "knock1.wav"


@dataclass
class WebConfig:
    host: str = "0.0.0.0"  # 0.0.0.0 = reachable from other devices on the WLAN
    port: int = 8080


@dataclass
class Config:
    audio: AudioConfig
    trigger: TriggerConfig
    behavior: BehaviorConfig
    knock: KnockConfig
    web: WebConfig
    language: str = "en"
    volume: float = 0.9

    @property
    def model_name(self) -> str:
        if self.language not in MODEL_NAMES:
            raise ValueError(
                f"Unsupported language '{self.language}'. Known: {list(MODEL_NAMES)}"
            )
        return MODEL_NAMES[self.language]

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG_PATH) -> "Config":
        raw = yaml.safe_load(Path(path).read_text())

        behavior_raw = raw.get("behavior", {})
        profiles = {
            name: SpontaneousProfile(**profile_raw)
            for name, profile_raw in behavior_raw.get("profiles", {}).items()
        }
        behavior = BehaviorConfig(mode=behavior_raw.get("mode", "responsive"), profiles=profiles)

        return cls(
            audio=AudioConfig(**raw.get("audio", {})),
            trigger=TriggerConfig(**raw.get("trigger", {})),
            behavior=behavior,
            knock=KnockConfig(**raw.get("knock", {})),
            web=WebConfig(**raw.get("web", {})),
            language=raw.get("language", "en"),
            volume=raw.get("volume", 0.9),
        )
