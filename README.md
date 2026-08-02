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
  (responsive/distress/weak) live — no restart needed — adjust voice and
  knock volume independently, toggle "Knocking mode" (see below), and a
  "Trigger now" button to fire a test response on demand

Switching language reloads the Vosk model and restarts the mic stream in
the background (briefly shows "not ready" while it happens); switching
mode, volume, or knocking mode takes effect on the very next response.

### Knocking mode

`knock.loop_enabled` in `config.yaml` (or the "Knocking mode" checkbox on
the dashboard) makes the knock clip repeat — `knock.loop_count` times, with
`knock.loop_gap_seconds` of silence between repeats — instead of a single
knock. Turning it on is a deliberate action: it also guarantees a knock on
every response for as long as it's enabled, bypassing `knock.probability`
(and the per-mode `knock_probability` in `behavior.profiles`) — the point
is a reliable, obvious continuous-knocking demo/test, not another random
chance. Turn it back off to return to normal probabilistic single knocks.

### Independent voice/knock volume

Voice (shout/cry/moan) and knock volume are separate: `volume.voice` and
`volume.knock` in `config.yaml`, or the two sliders on the dashboard. Both
are further scaled by the active behavior profile's `volume_multiplier`
(e.g. `weak` mode quiets both proportionally) but are otherwise independent
— handy for balancing a loud transducer against a quieter speaker, or vice
versa, without one affecting the other.

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

- Deployed and running on the real Pi 4B (`rvsp4`): HiFiBerry DAC+ and
  ReSpeaker Lite both confirmed working over ALSA (`aplay -l` / `arecord -l`)
  — the ReSpeaker needed its USB-audio firmware flashed via `dfu-util`
  first (see the "ReSpeaker Lite" step under Deploying below), after which
  it shows up as a normal 2-channel capture device. `config.yaml`'s
  `audio.input_device` / `output_device` are set to match.
- Web dashboard live-tested end to end on both the dev laptop and the Pi:
  real HTTP requests and a real browser — status/log endpoints,
  mode/language/volume changes, independent voice/knock volume, "Knocking
  mode" (looped knock, confirmed audibly longer than a single knock), and
  the "Trigger now" button all confirmed working, including a
  `distress`-mode spontaneous call firing on its own mid-session
- `scripts/smoke_test.py` and `pytest` (`tests/`) both pass — clip
  loading/mixing, `loop_clip`, device listing, both languages/Vosk models,
  `Responder` in all three behavior modes (with independent volumes and
  knocking mode), `SpontaneousLoop` start/fire/stop, and the Flask
  dashboard's routes + input validation
- Real recordings now in `assets/sounds/` (multiple takes per category:
  `shout01-03.wav`, `cry01.wav`, `moan01.wav`, `knock01-03.wav`), replacing
  the synthesized placeholders — `SoundBank` picks randomly within each
  category, knock included, so more takes can be dropped in any time
- **Not yet tested**: a live "say hello, hear it respond" session over
  actual WLAN from a second device with a person speaking near the
  ReSpeaker (dashboard + manual trigger are confirmed; the full mic ->
  keyword-match -> speaker loop on hardware isn't yet).

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

It's a USB device — plug it in and check `arecord -l`. **If it doesn't show
up there** (but `lsusb`/`dmesg` shows it enumerating with
`idVendor=2886, idProduct=0019, Product: ReSpeaker Lite`), it likely shipped
with I2S-mode firmware instead of USB-audio-mode firmware, and needs
reflashing:

```bash
sudo apt-get install -y dfu-util
sudo dfu-util -l   # confirm it shows 2886:0019 DFU interfaces
wget https://raw.githubusercontent.com/respeaker/ReSpeaker_Lite/master/xmos_firmwares/respeaker_lite_usb_dfu_firmware_v2.0.7.bin
sudo dfu-util -R -e -a 1 -D respeaker_lite_usb_dfu_firmware_v2.0.7.bin
```

Unplug/replug (or reboot) after flashing, then `arecord -l` should show it
as a standard USB Audio Class 2.0 card (e.g. `card N: Lite [ReSpeaker Lite]`).
It exposes 2 raw capture channels (`audio.mic_channels: 2` in `config.yaml`
— the listener averages them down to mono before feeding the recognizer).

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

`assets/sounds/` now has real recordings, not the synthesized placeholders
(shout/cry/moan/knock — multiple takes each). Add more takes any time by
dropping `.wav` files into `assets/sounds/<shout|cry|moan|knock>/` — any
filename works, `SoundBank` picks randomly within each folder (knock
included) and avoids repeating the same clip twice in a row.

For a fresh checkout with no recordings yet,
`scripts/generate_placeholder_sounds.py` synthesizes rough stand-in tones
(siren-like "shout", wavering "cry", low "moan", triple-thump "knock") just
so the pipeline has something to play while you're setting up.

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
- Real recordings only cover one or a few takes per category so far — more
  variety (and a genuinely weak/exhausted-sounding take for `weak` mode)
  would help against repetition during longer training sessions.
- Dashboard has no authentication and uses Flask's built-in dev server —
  fine for a trusted training-exercise WLAN, not for exposing beyond that.
- Dashboard log is in-memory only (last 300 events, process lifetime) —
  add persistence if you need to review a session after the Pi restarts.