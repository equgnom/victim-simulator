# Responsive Victim Simulator

A training aid for search-and-rescue exercises: a Raspberry Pi listens for
rescuers ("Hello? Anyone there?") and responds with a victim sound (shout,
cry, moan) through a loudspeaker, and can drive a knock transducer as a
secondary cue.

## How it works

```
ReSpeaker Lite (USB mic array)
        |
        v
  KeywordListener (Vosk, offline speech recognition)
        |  keyword detected ("hello", "rescue", ...)
        v
     Responder  --cooldown-->  SoundBank (random clip, no immediate repeat)
        |
        v
  stereo mix: voice -> left channel, knock -> right channel
        |
        v
   HiFiBerry (audio HAT) -> loudspeaker (left) + amp -> transducer (right)
```

While a response is playing, the listener is muted (see `Responder.busy` in
[`src/victimsim/responder.py`](src/victimsim/responder.py)) so the sim
doesn't hear and re-trigger on itself — there's no hardware AEC in this v1.

## Status as of today

Built and smoke-tested on this dev laptop (not the Pi yet):
- Project scaffolded, Python deps installed in `.venv`
- Placeholder audio (synthesized tones, not real recordings) in `assets/sounds/`
- Vosk small English model downloaded to `assets/models/`
- `scripts/smoke_test.py` passes: clip loading, stereo mixing, device listing,
  Vosk recognizer, and playback through the default output all work
- **Not yet tested**: a real live "say hello, hear it respond" loop needs a
  human at a mic — run `python -m victimsim.main` and try it interactively.

## Quickstart (dev laptop or Pi — same steps)

```bash
cd victim-simulator
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/generate_placeholder_sounds.py   # or drop in real recordings
bash scripts/download_vosk_model.sh
PYTHONPATH=src .venv/bin/python -m victimsim.main --list-devices
```

Note what your mic and speaker devices are called, then edit
[`config.yaml`](config.yaml) `audio.input_device` / `audio.output_device` to
substrings that uniquely match them. Then run it:

```bash
PYTHONPATH=src .venv/bin/python -m victimsim.main
```

Say "hello" (or any phrase in `trigger.keywords`) near the mic — it should
respond with a random shout/cry/moan, and sometimes a knock.

If `sounddevice` fails to import with a `libportaudio` error, install
PortAudio first (`sudo apt-get install libportaudio2`).

## Deploying to the Pi 4B

1. **HiFiBerry**: add the correct overlay for your specific HiFiBerry board
   to `/boot/firmware/config.txt`, e.g. for a HiFiBerry AMP2:
   `dtoverlay=hifiberry-amp` (check Modmyi/HiFiBerry docs for your exact
   model — DAC+, AMP2, AMP4 etc. all use different overlay names). Disable
   the onboard audio if instructed (`dtparam=audio=off`), then reboot and
   confirm with `aplay -l` that the HiFiBerry card shows up.
2. **ReSpeaker Lite**: it's a USB device, should enumerate automatically —
   confirm with `arecord -l`. It may expose 1 or 2 capture channels
   depending on firmware mode; if it's 2, set `audio.mic_channels: 2` in
   `config.yaml` (the listener averages multi-channel input down to mono
   before feeding the recognizer).
3. **Knocking transducer**: wired to the second HiFiBerry output channel
   through an amp (per your Q&A choice) — no GPIO needed. If your HiFiBerry
   board is mono-only, you'll need a different transducer output path (a
   second small amp off a GPIO PWM pin, or a second audio HAT) — the code's
   `knock_channel` config exists so this is a one-line config change once
   you decide.
4. Re-run the Quickstart steps above on the Pi itself, then point
   `audio.input_device` / `audio.output_device` at the real device names
   from `--list-devices` (e.g. `"ReSpeaker"` / `"snd_rpi_hifiberry"`).
5. For auto-start on boot, wrap `python -m victimsim.main` in a systemd
   service (not set up yet — ask when you get there).

## Replacing placeholder sounds

`scripts/generate_placeholder_sounds.py` synthesizes rough stand-in tones
(siren-like "shout", wavering "cry", low "moan", triple-thump "knock") just
so the pipeline has something to play. Swap them for real recordings by
dropping `.wav` files into `assets/sounds/<shout|cry|moan|knock>/` — any
filename works, `SoundBank` picks randomly within each folder and avoids
repeating the same clip twice in a row.

## Known limitations / next steps

- No acoustic echo cancellation — relies on a hard mute during playback.
  If the ReSpeaker Lite's onboard AEC turns out to be good enough, this
  mute could be relaxed for more natural back-and-forth.
- Keyword list is a blunt substring match on Vosk's transcription — works
  in quiet/moderate noise, may need retuning (or a fallback sound-level
  trigger) once tested in a realistic outdoor SAR training environment.
- Single hardcoded English model; add more phrases/languages by editing
  `trigger.keywords` in `config.yaml` and swapping the Vosk model.
- Placeholder audio is synthesized, not recorded — plan a real
  voice-actor/foley session before using this for actual training exercises.
