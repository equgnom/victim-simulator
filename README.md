# Responsive Victim Simulator

A training aid for search-and-rescue exercises: a Raspberry Pi listens for
rescuers ("Hello? Anyone there?" / German supported too) and responds with a
victim sound (shout, cry, moan) through a loudspeaker, and can drive a knock
transducer as a secondary cue. It can also be switched into a mode where the
victim calls out and knocks on its own, without waiting to be spoken to. A
web dashboard, reachable from any device on the same WLAN, shows live status
and a recognition log, and lets you change language/mode/volume or fire a
test response without touching the Pi.

## How it works

```
ReSpeaker Lite (USB mic array)
        |
        v
  KeywordListener (Vosk, offline speech recognition, en or de)
        |  keyword detected ("hello"/"hallo", "rescue"/"rettung", ...)
        v
     Responder  --cooldown-->  SoundBank (random clip, no immediate repeat)
        ^                              |
        |  (distress/weak modes only)  v
  SpontaneousLoop -- random-interval  stereo mix: voice -> left channel,
  timer, self-triggers Responder      knock -> right channel
                                              |
                                              v
                          HiFiBerry (audio HAT) -> loudspeaker (left)
                                             + amp -> transducer (right)

  SharedState (status, event log) <--- both KeywordListener and Responder
        ^                                report into it
        |
  Web dashboard (Flask, reachable over WLAN) -- reads status/log,
  writes language/mode/volume/manual-trigger back into SharedState
```

While a response is playing, the listener is muted (see `Responder.busy` in
[`src/victimsim/responder.py`](src/victimsim/responder.py)) so the sim
doesn't hear and re-trigger on itself — there's no hardware AEC in this v1.

## Web dashboard

Run `python -m victimsim.main` and it starts a web server (Flask) alongside
the audio loop. From any device on the same WLAN as the Pi:

```
http://<pi-ip>:8080
```

(the terminal running `main.py` prints the exact URLs, including the WLAN
IP, on startup). The dashboard shows:

- **Status**: listener ready/not, uptime, last heard phrase, last response
  (category + whether it knocked), heard/response counts, CPU temperature
- **Live log**: every recognized phrase, every response (and why it fired —
  keyword match, spontaneous call, or manual trigger), cooldown-ignored
  hits, and system events (listener ready, mode/language changes)
- **Controls**: switch language (en/de) or behavior mode
  (responsive/distress/weak) live — no restart needed — adjust volume, and
  a "Trigger now" button to fire a test response on demand

Switching language reloads the Vosk model and restarts the mic stream in
the background (briefly shows "not ready" while it happens); switching mode
or volume takes effect on the very next response.

No authentication — this is meant for a trusted local WLAN during a
training exercise, not the open internet. If you need to lock it down
further: set `web.host: 127.0.0.1` in `config.yaml` and reach it only via
SSH port-forwarding, or put it behind a reverse proxy with auth. The
built-in server (Flask's dev server) is fine for this use case but isn't
hardened for production/public exposure.

Run `python -m victimsim.main --no-web` for CLI-only mode (no dashboard).

## Language

Set `language: en` or `language: de` in [`config.yaml`](config.yaml). This
picks both the keyword list under `trigger.keywords` and the Vosk model
(`assets/models/vosk-model-small-en-us-0.15` or `-de-0.15`) — or switch it
live from the web dashboard. Download the model for whichever language(s)
you use:

```bash
bash scripts/download_vosk_model.sh en
bash scripts/download_vosk_model.sh de
```

Add more phrases by editing `trigger.keywords.en` / `trigger.keywords.de` in
`config.yaml` (case-insensitive substring match against what Vosk hears).

## Behavior modes

`behavior.mode` in `config.yaml` (or the dashboard's Mode selector) controls
how "alive" the victim is:

- **`responsive`** (default) — only reacts when it hears a keyword.
- **`distress`** — additionally calls out on its own (shout/cry/moan + knock)
  at normal strength, on a random timer (`interval_min_seconds` /
  `interval_max_seconds` under `behavior.profiles.distress`). Simulates a
  victim actively shouting and knocking for help.
- **`weak`** — same idea, but rarely (much longer interval) and at reduced
  volume, restricted to weaker-sounding categories (moan/cry, no shout), and
  a lower knock chance. Simulates an exhausted/weak victim.

In `distress`/`weak` modes, the listener keeps running too — the victim
still responds to being spoken to directly, and does so at that mode's
strength (a weak victim sounds weak whether it called out on its own or was
just answered to). Tune the numbers per mode under `behavior.profiles` in
`config.yaml`.

## Status as of today

Built and smoke-tested on this dev laptop (not the Pi yet):
- Project scaffolded, Python deps installed in `.venv`
- Placeholder audio (synthesized tones, not real recordings) in `assets/sounds/`
- Vosk small English *and* German models downloaded to `assets/models/`
- Web dashboard live-tested end to end: ran `python -m victimsim.main`,
  hit it with real HTTP requests and a real browser — status/log endpoints,
  mode/language/volume changes, and the "Trigger now" button all confirmed
  working, including a `distress`-mode spontaneous call firing on its own
  mid-session
- `scripts/smoke_test.py` and `pytest` (`tests/`) both pass — clip
  loading/mixing, device listing, both languages/Vosk models, `Responder`
  in all three behavior modes, `SpontaneousLoop` start/fire/stop, and the
  Flask dashboard's routes + input validation
- **Not yet tested on real hardware**: the ReSpeaker Lite mic array and
  HiFiBerry output, and a live "say hello, hear it respond" session over
  actual WLAN from a second device — needs the Pi.

## Quickstart (dev laptop or Pi — same steps)

```bash
cd victim-simulator
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/generate_placeholder_sounds.py   # or drop in real recordings
bash scripts/download_vosk_model.sh en                    # or: de
PYTHONPATH=src .venv/bin/python -m victimsim.main --list-devices
```

Note what your mic and speaker devices are called, then edit
[`config.yaml`](config.yaml) `audio.input_device` / `audio.output_device` to
substrings that uniquely match them. Then run it:

```bash
PYTHONPATH=src .venv/bin/python -m victimsim.main
```

Open the printed dashboard URL in a browser, or say "hello" (or any phrase
in `trigger.keywords.<language>`) near the mic — it should respond with a
random shout/cry/moan, and sometimes a knock. Switch to `distress` or `weak`
mode (in `config.yaml` or the dashboard) to also have it call out on its own.

If `sounddevice` fails to import with a `libportaudio` error, install
PortAudio first (`sudo apt-get install libportaudio2`).

## Deploying to the Pi 4B

### 0. Flash the OS

Use Raspberry Pi Imager to write Raspberry Pi OS (Bookworm, 64-bit
recommended). In the imager's advanced options (gear icon) before writing,
set: hostname, enable SSH (with your public key or a password), and your
WLAN SSID/password. This gets you headless SSH access on first boot — no
monitor/keyboard needed.

### 1. First boot + base packages

```bash
ssh pi@<pi-hostname-or-ip>.local
sudo apt-get update
sudo apt-get install -y git python3-venv python3-pip libportaudio2 unzip
```

### 2. HiFiBerry overlay

Add the correct overlay for your specific HiFiBerry board to
`/boot/firmware/config.txt`, e.g. for a HiFiBerry AMP2:
`dtoverlay=hifiberry-amp` (check HiFiBerry's docs for your exact model —
DAC+, AMP2, AMP4 etc. all use different overlay names). Disable the onboard
audio if instructed (`dtparam=audio=off`), then reboot and confirm with
`aplay -l` that the HiFiBerry card shows up.

### 3. ReSpeaker Lite

It's a USB device, should enumerate automatically — plug it in and confirm
with `arecord -l`. It may expose 1 or 2 capture channels depending on
firmware mode; if it's 2, you'll set `audio.mic_channels: 2` in
`config.yaml` in step 5 (the listener averages multi-channel input down to
mono before feeding the recognizer).

### 4. Knocking transducer

Wired to the second HiFiBerry output channel through an amp (per your
earlier choice) — no GPIO needed. If your HiFiBerry board is mono-only,
you'll need a different transducer output path (a second small amp off a
GPIO PWM pin, or a second audio HAT) — the code's `knock_channel` config
exists so this is a one-line config change once you decide.

### 5. Get the code running

```bash
git clone https://github.com/equgnom/victim-simulator.git
cd victim-simulator
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/generate_placeholder_sounds.py   # or scp real recordings into assets/sounds/
bash scripts/download_vosk_model.sh en
bash scripts/download_vosk_model.sh de
PYTHONPATH=src .venv/bin/python -m victimsim.main --list-devices
```

Note the exact input/output device names from that output, then edit
`config.yaml`: set `audio.input_device` / `audio.output_device` to
substrings that uniquely match them (e.g. `"ReSpeaker"` / `"snd_rpi_hifiberry"`),
and `audio.mic_channels` per step 3.

### 6. Try it

```bash
PYTHONPATH=src .venv/bin/python -m victimsim.main
```

It prints the dashboard URL including the Pi's WLAN IP — open that from a
phone or laptop on the same network. If the Pi has a firewall (`ufw`)
enabled, allow the port first: `sudo ufw allow 8080/tcp`. Say a keyword near
the mic, or use the dashboard's "Trigger now" button, to confirm playback
through the real speaker/transducer.

### 7. Auto-start on boot

A systemd unit is included at [`deploy/victimsim.service`](deploy/victimsim.service).
Edit its `User=`, `WorkingDirectory=`, and `Environment=PYTHONPATH=...` if
your username or clone path differ from `pi` / `/home/pi/victim-simulator`,
then:

```bash
sudo cp deploy/victimsim.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now victimsim
sudo systemctl status victimsim      # confirm it's running
journalctl -u victimsim -f           # tail its logs
```

It restarts automatically on failure and starts on every boot. To make
config changes after this, edit `config.yaml` and
`sudo systemctl restart victimsim` (or just use the dashboard for anything
that's live-adjustable: language, mode, volume).

## Running tests

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q       # unit tests (tests/)
.venv/bin/python scripts/smoke_test.py              # end-to-end smoke test
```

The smoke test exercises every module (config parsing, clip loading/mixing,
both Vosk models, the responder in all three behavior modes, the
spontaneous-call loop, and the Flask dashboard's routes) without needing a
live mic conversation or a running server.

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
- Only English and German models are wired up; add more by extending
  `MODEL_NAMES` in `src/victimsim/config.py`, the `case` in
  `scripts/download_vosk_model.sh`, and a `trigger.keywords.<lang>` list.
- Placeholder audio is synthesized, not recorded — plan a real
  voice-actor/foley session before using this for actual training exercises.
- Dashboard has no authentication and uses Flask's built-in dev server —
  fine for a trusted training-exercise WLAN, not for exposing beyond that.
- Dashboard log is in-memory only (last 300 events, process lifetime) —
  add persistence if you need to review a session after the Pi restarts.