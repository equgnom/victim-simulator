"""Entry point: wires the keyword listener to the responder and loops forever.

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

DEFAULT_MODEL_DIR = MODELS_DIR / "vosk-model-small-en-us-0.15"


def main() -> None:
    parser = argparse.ArgumentParser(description="Responsive Victim Simulator")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config.yaml")
    parser.add_argument("--model", default=str(DEFAULT_MODEL_DIR), help="Path to Vosk model dir")
    args = parser.parse_args()

    if args.list_devices:
        print(audio_hal.list_devices())
        return

    config = Config.load(args.config)
    input_device = audio_hal.resolve_device(config.audio.input_device, "input")
    output_device = audio_hal.resolve_device(config.audio.output_device, "output")

    model_path = Path(args.model)
    if not model_path.exists():
        print(
            f"Vosk model not found at {model_path}.\n"
            f"Run: bash scripts/download_vosk_model.sh",
            file=sys.stderr,
        )
        sys.exit(1)

    listener = KeywordListener(
        model_path=model_path,
        samplerate=config.audio.mic_sample_rate,
        device=input_device,
        keywords=config.trigger.keywords,
        channels=config.audio.mic_channels,
    )
    sound_bank = SoundBank()
    responder = Responder(config, sound_bank, output_device)

    print("Responsive Victim Simulator running. Listening for:", config.trigger.keywords)
    print("Press Ctrl+C to stop.")
    try:
        while True:
            keyword, text = listener.wait_for_keyword(mute_event=responder.busy)
            if responder.ready():
                responder.respond(keyword, text)
            else:
                print(f"[cooldown] heard '{text}' but still cooling down, ignoring")
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
