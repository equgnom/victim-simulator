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
    "it": "vosk-model-small-it-0.22",
}


@dataclass
class AudioConfig:
    input_device: str | None = None
    output_device: str | None = None
    mic_channels: int = 1     # ReSpeaker Lite may expose 2 channels; averaged down to mono for the recognizer
    mic_sample_rate: int = 16000
    # Audio fed to the recognizer in chunks this many samples long (at
    # mic_sample_rate). Smaller = checks for a keyword match more often =
    # lower detection latency, at the cost of more frequent recognizer
    # calls. 3200 @ 16kHz = 0.2s.
    mic_block_size: int = 3200
    playback_sample_rate: int = 44100
    voice_channel: str = "left"
    knock_channel: str = "right"


@dataclass
class TriggerConfig:
    keywords: dict[str, list[str]] = field(default_factory=dict)
    cooldown_seconds: float = 5.0
    response_categories: list[str] = field(default_factory=lambda: ["shout", "cry", "moan"])
    # Subset of response_categories currently eligible to be picked — a
    # global on/off per category, adjustable live from the dashboard
    # (checked = included). Defaults to all of response_categories when
    # left empty. Applies on top of whatever a behavior profile's own
    # response_categories says (see Responder._strength_params).
    enabled_categories: list[str] = field(default_factory=list)

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
    # "Knocking mode": loop the knock clip instead of playing it once. While
    # enabled, a knock is guaranteed on every response (probability above is
    # bypassed) — turning this on is a deliberate "make it knock" action.
    loop_enabled: bool = False
    loop_count: int = 4
    loop_gap_seconds: float = 0.4


@dataclass
class VolumeConfig:
    voice: float = 0.9
    knock: float = 0.9


@dataclass
class WebConfig:
    host: str = "0.0.0.0"  # 0.0.0.0 = reachable from other devices on the WLAN
    port: int = 8080


@dataclass
class MonitoringConfig:
    # The Pi throttles ARM core frequency starting ~80°C and hard-limits at
    # 85°C (official Raspberry Pi thermal behavior). Default here is a few
    # degrees below that, so the dashboard warns before throttling actually
    # starts, not exactly when it does.
    cpu_temp_warning_c: float = 75.0


@dataclass
class ApModeConfig:
    """Declares the desired standalone-WiFi-hotspot state. Applying it is a
    separate, explicit step (scripts/setup_wifi_ap.sh) — this config is just
    the single source of truth for SSID/password so the app and the setup
    script never disagree."""

    enabled: bool = False
    ssid: str = "VictimSim"
    password: str = "rescue1234"
    interface: str = "wlan0"


@dataclass
class NetworkConfig:
    ap_mode: ApModeConfig = field(default_factory=ApModeConfig)


@dataclass
class Config:
    audio: AudioConfig
    trigger: TriggerConfig
    behavior: BehaviorConfig
    knock: KnockConfig
    web: WebConfig
    volume: VolumeConfig
    network: NetworkConfig
    monitoring: MonitoringConfig
    language: str = "en"

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

        volume_raw = raw.get("volume", 0.9)
        if isinstance(volume_raw, dict):
            volume = VolumeConfig(**volume_raw)
        else:
            # Back-compat with the old flat `volume: 0.9` format: same level
            # applied to both voice and knock.
            volume = VolumeConfig(voice=float(volume_raw), knock=float(volume_raw))

        network_raw = raw.get("network", {})
        network = NetworkConfig(ap_mode=ApModeConfig(**network_raw.get("ap_mode", {})))

        trigger = TriggerConfig(**raw.get("trigger", {}))
        if not trigger.enabled_categories:
            trigger.enabled_categories = list(trigger.response_categories)

        return cls(
            audio=AudioConfig(**raw.get("audio", {})),
            trigger=trigger,
            behavior=behavior,
            knock=KnockConfig(**raw.get("knock", {})),
            web=WebConfig(**raw.get("web", {})),
            volume=volume,
            network=network,
            monitoring=MonitoringConfig(**raw.get("monitoring", {})),
            language=raw.get("language", "en"),
        )
