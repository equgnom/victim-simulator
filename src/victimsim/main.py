"""Entry point: wires the keyword listener (and, in distress/weak modes, the
spontaneous self-caller) to the responder and loops forever.

Usage:
    python -m victimsim.main --list-devices     # find device indices/names
    python -m victimsim.main                    # run the simulator
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import audio_hal
from .config import Config, DEFAULT_CONFIG_PATH, MODELS_DIR
from .listener import KeywordListener
from .responder import Responder
from .sound_bank import SoundBank
from .spontaneous import SpontaneousCaller


def main() -> None:
    parser = argparse.ArgumentParser(description="Responsive Victim Simulator")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config.yaml")
    parser.add_argument(
        "--model",
        default=None,
        help="Path to Vosk model dir (default: assets/models/<model for config's language>)",
    )
    args = parser.parse_args()

    if args.list_devices:
        print(audio_hal.list_devices())
        return

    config = Config.load(args.config)
    input_device = audio_hal.resolve_device(config.audio.input_device, "input")
    output_device = audio_hal.resolve_device(config.audio.output_device, "output")

    model_path = Path(args.model) if args.model else MODELS_DIR / config.model_name
    if not model_path.exists():
        print(
            f"Vosk model not found at {model_path}.\n"
            f"Run: bash scripts/download_vosk_model.sh {config.language}",
            file=sys.stderr,
        )
        sys.exit(1)

    keywords = config.trigger.keywords_for(config.language)
    listener = KeywordListener(
        model_path=model_path,
        samplerate=config.audio.mic_sample_rate,
        device=input_device,
        keywords=keywords,
        channels=config.audio.mic_channels,
    )
    sound_bank = SoundBank()
    responder = Responder(config, sound_bank, output_device)

    spontaneous_caller = None
    profile = config.behavior.active_profile()
    if profile is not None:
        spontaneous_caller = SpontaneousCaller(responder, profile)
        spontaneous_caller.start()

    print(
        f"Responsive Victim Simulator running (language={config.language}, "
        f"mode={config.behavior.mode}). Listening for:", keywords,
    )
    print("Press Ctrl+C to stop.")
    try:
        while True:
            keyword, text = listener.wait_for_keyword(mute_event=responder.busy)
            if responder.ready():
                responder.respond(f"heard '{text}' (matched '{keyword}')")
            else:
                print(f"[cooldown] heard '{text}' but still cooling down, ignoring")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if spontaneous_caller is not None:
            spontaneous_caller.stop()


if __name__ == "__main__":
    main()
