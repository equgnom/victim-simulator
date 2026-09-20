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
  knock volume independently, toggle "Knocking mode" (see below), a
  "Trigger now" button to fire a test response on demand, "Knock on every
  response", and "Reset to defaults" (see the sections below)

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

### Knock on every response

The dashboard's **Knock on every response** button forces the chance that a
response includes a knock to 100% — a single knock per response, unlike
Knocking mode, which loops it. Each mode has its own configured chance
(`knock.probability` for responsive, `knock_probability` in each
`behavior.profiles` entry), so rather than overwrite those it sets an
override on top: it works the same in every mode, your tuned numbers stay
untouched, and clicking the button again clears the override and puts them
back. The "Knock chance" status card always shows the effective value in the
current mode (`50%`, `100% (forced)`, `100% (knocking mode)`). Like the
other dashboard settings it's saved across restarts.

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

Set `language: en`, `de`, or `it` in [`config.yaml`](config.yaml). This
picks both the keyword list under `trigger.keywords` and the Vosk model
(`assets/models/vosk-model-small-<en-us-0.15|de-0.15|it-0.22>`) — or switch
it live from the web dashboard. Download the model for whichever
language(s) you use:

```bash
bash scripts/download_vosk_model.sh en
bash scripts/download_vosk_model.sh de
bash scripts/download_vosk_model.sh it
```

**The models are not in git.** They're ~70-90 MB each and gitignored
(`assets/models/`), so `git pull` never brings them: every device — the Pi
included — must run the download command above for each language it should
use, once, and it needs internet (Ethernet or client WiFi, not the AP). Pull
a commit that adds a language and the dashboard will offer it, but it won't
work until the model is downloaded on that device. The dashboard makes this
hard to miss rather than silent:

- languages whose model isn't installed show as `Italian (not installed)`
  and can't be selected;
- if a choice is refused anyway (e.g. a stale page), a red banner says the
  language was **not** changed and shows the exact download command, the
  dropdown snaps back to the language that's really running, and the
  refusal is written to the log;
- at startup, a `config.yaml` language (or saved dashboard language) whose
  model is missing is reported once — `NOT LISTENING: Vosk model for 'it'
  not found ...` in `journalctl -u victimsim` and the dashboard log — and
  the listener card shows "not ready".

Add more phrases by editing `trigger.keywords.<en|de|it>` in `config.yaml`
(case-insensitive substring match against what Vosk hears). Add another
language entirely by extending `MODEL_NAMES` in
`src/victimsim/config.py`, the `case` in `scripts/download_vosk_model.sh`,
a `trigger.keywords.<lang>` list, and the `<option>` in the dashboard's
language selector ([`templates/index.html`](src/victimsim/templates/index.html)).

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
strength: volume and knock chance follow the mode, so a weak victim answers
quietly just like it calls quietly. (Which *sound* plays for a keyword is
separate — see "Keyword replies".) Tune the numbers per mode under
`behavior.profiles` in `config.yaml`.

## Keyword replies

When the listener hears a keyword, the victim answers with **dedicated reply
sounds** from `assets/sounds/reply/` — separate from `shout/`, `cry/` and
`moan/`, which are the victim's own spontaneous calls (`distress`/`weak`
mode). Drop `.wav` files into `assets/sounds/reply/` (any filename; one is
picked at random each time, never the same one twice in a row) — see the
`README.txt` in that folder. Files added while the simulator is running are
used without a restart.

**Until real reply recordings exist, the `cry` sounds stand in** (the folder
is empty for now), so a keyword is never answered with silence. It switches
over by itself the moment a `.wav` appears in `reply/` — nothing to delete or
reconfigure. Which folders are used is `trigger.reply_category` (default
`reply`) and `trigger.reply_fallback_category` (default `cry`) in
`config.yaml`. You can see what's in effect at a glance: the dashboard's
**Keyword reply** card shows `stand-in: cry (no reply files yet)` or
`reply: N files`, the startup output says the same, and each stand-in play is
marked `[stand-in: ...]` in the log.

What still applies to a reply: the current mode's volume multiplier and knock
chance, the voice/knock volume sliders, the cooldown, knocking mode and
"Knock on every response". What doesn't: the *Voice sounds* checkboxes and a
mode's own category list — those choose among spontaneous-call sounds, so
unchecking "Cry" won't silence the cry *stand-in* for replies (it stops
mattering once real reply files are in). The dashboard's **Trigger now**
button is a generic test and still plays a random shout/cry/moan, not a reply.

## Voice sound selection

`trigger.enabled_categories` in `config.yaml`, or the "Voice sounds"
checkboxes on the dashboard (Shout/Cry/Moan) — check a category to make it
eligible for random selection, uncheck to exclude it from every mode's
response entirely. Applies on top of whatever the active behavior profile
would otherwise offer (so `weak` mode's own moan/cry-only restriction still
holds; unchecking one of those two just narrows it further). At least one
category must always stay enabled — the dashboard rejects trying to
uncheck the last one, with an inline error explaining why.

These choose among the victim's *spontaneous-call* sounds (`distress`/`weak`
mode) and the "Trigger now" test button. Keyword hits are answered from their
own dedicated set instead — see "Keyword replies".

## Cooldown

`trigger.cooldown_seconds` in `config.yaml` (default `5`), or the
"Cooldown" slider on the dashboard — the minimum gap between any two
responses, keyword-triggered or spontaneous, so it doesn't spam. The
dashboard slider covers 0-10s (whole seconds) with a live numeric readout
while dragging; it posts the change on release, not on every pixel of
drag. `config.yaml` and the API still accept anything up to 300s — if the
configured value is above 10 the slider just pins at its right end while
the readout keeps showing the real value.

## Persistent dashboard settings

Everything you change from the dashboard — language, behavior mode, voice
and knock volume, knocking mode, the knock-chance override, cooldown, and
the voice-sound checkboxes — is saved immediately to `runtime_settings.json` in the repo root and
re-applied on the next start. So a service restart, a crash-loop recovery,
a power cycle, or a lost WiFi/phone connection all come back up with your
latest configuration instead of falling back to `config.yaml`. (Losing the
WiFi connection alone never reset anything — the app just keeps running with
its in-memory settings — but a restart used to.)

- **Precedence:** `config.yaml` provides the defaults, `runtime_settings.json`
  layers on top of it. That means editing one of these eight values in
  `config.yaml` has *no visible effect* once it's been saved from the
  dashboard — the saved value wins. The startup log/console says
  `restored saved dashboard settings from runtime_settings.json: ...` so
  you can tell when that's happening.
- **Reset to `config.yaml`'s values:** the dashboard's **Reset to defaults**
  button (asks for confirmation). It puts all eight settings back to what
  `config.yaml` says, takes effect immediately (switching the listener's
  language back if needed), and deletes the saved file so a restart doesn't
  bring the old values back. Without a browser at hand, the equivalent is
  `rm ~/victim-simulator/runtime_settings.json && sudo systemctl restart victimsim`.
- **Not synced by git:** the file is gitignored, so `git pull` on the Pi
  never conflicts with it (unlike `config.yaml`) and the Pi keeps its own.
  Anything else in `config.yaml` (devices, keywords, behavior profiles,
  AP mode, ...) isn't dashboard-adjustable and isn't affected.
- **Safe against bad files:** writes are atomic and fsynced (a power cut
  mid-write can't leave a truncated file), and loading is forgiving — a
  corrupt file, unknown keys, out-of-range values, or a saved language
  whose Vosk model isn't installed are ignored individually and
  `config.yaml`'s value is used for them, so it can never stop the
  simulator from starting. If the file can't be written (full or read-only
  SD card) the dashboard keeps working and the failure shows up in the log.
- `--settings-file PATH` overrides the location (handy for testing).

## CPU temperature warning

The Raspberry Pi throttles its ARM core frequency starting around 80°C and
hard-limits both ARM and GPU at 85°C (official Raspberry Pi thermal
behavior — there's no shutdown, it just clocks down to stay under that
ceiling). The dashboard's "CPU temp" card turns into a visible warning
(orange border, warning text) once the temperature reaches
`monitoring.cpu_temp_warning_c` in `config.yaml` (default `75.0`, a few
degrees ahead of actual throttling so you notice in time). Crossing into
or recovering out of the warning zone also gets a one-time log entry —
it's edge-triggered, not logged on every 2-second poll.

This is Pi SoC temperature only (`/sys/class/thermal/thermal_zone0/temp`)
— the HiFiBerry board itself doesn't expose any temperature telemetry to
Linux (checked: its ALSA mixer only has volume-type controls), so there's
no equivalent warning possible for the amp/DAC chip itself.

## Log time vs. the Pi's clock

The Pi has no battery-backed RTC, and in standalone AP mode it has no
internet for NTP either — its system clock can end up wrong, sometimes by
hours (it just keeps whatever time it had when it lost power/network, via
`fake-hwclock`; if that was itself never synced, it can be arbitrarily
off). Rather than show that raw, possibly-wrong time, the dashboard
compares the server's reported time (`server_time` in `/api/status`)
against the viewing device's own clock on every poll, and shifts every
displayed timestamp (log entries, last-heard/last-response) by that
offset — so what you see matches your phone/laptop's clock, not the Pi's.
The "Clock" status card shows the current offset (`in sync`, or e.g.
`+3h 17m 0s (Pi clock)`), so you can tell at a glance whether — and how
much — the Pi's clock is off, and it self-corrects live if the Pi's clock
later gets fixed (e.g. it regains internet and NTP syncs mid-session).

This only corrects what's *displayed* — it doesn't change the Pi's actual
system clock, so anything else that reads it directly (`journalctl`
timestamps, file mtimes) still shows the Pi's own, potentially-wrong time.
If you want the underlying clock itself fixed, connect the Pi to the
internet (client WiFi mode, not the AP) at least once before a session so
NTP can sync it — `fake-hwclock` then keeps it close across reboots even
without further internet access.

## Detection-to-response latency

Four changes cut the gap between a keyword being spoken and the response
playing:

1. **Partial-result matching** ([`listener.py`](src/victimsim/listener.py)) —
   the listener now checks Vosk's streaming *partial* hypothesis on every
   audio block, not just the finalized result after it detects
   end-of-utterance silence. Waiting for that endpoint was the single
   biggest source of latency (often several hundred ms to over a second);
   reacting to the in-progress guess skips that wait entirely. Trade-off:
   a partial hypothesis can still change before Vosk settles on it, so
   this can occasionally trigger a beat early on text that isn't quite
   final — acceptable here since an early/extra response just means one
   more shout, not a wrong reading.
2. **Smaller mic block size** (`audio.mic_block_size` in `config.yaml`,
   default `3200` = 0.2s @ 16kHz, down from 0.5s) — audio reaches the
   recognizer in smaller pieces, so there's less inherent delay between
   speech happening and it being checked at all.
3. **Clip preloading** — all sound clips are decoded and resampled into
   memory once at startup (prints `Preloaded N sound clips...`) instead of
   on first use. `load_clip()` is cached by (path, sample rate) regardless,
   but preloading means even the *first* response of a session doesn't
   pay that disk I/O + resample cost — it happens once, upfront, off the
   response path entirely.
4. **Low-latency audio streams** — both the mic input stream and
   `sd.play()` now request `latency="low"` from PortAudio, shrinking its
   default buffering.

Measured on this dev laptop: preloading moves ~28ms of disk I/O +
resample off the response path (paid once at startup instead) — likely
more pronounced on the Pi's SD card and slower CPU. The partial-result
change is the one that actually matters most, but it isn't independently
measurable without a live mic and a real spoken phrase; it needs to be
judged by ear on the actual hardware.

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
- Real recordings now in `assets/sounds/` (`free_shout.wav`,
  `free_crying.wav`, `free_sobbing.wav`, `knock01-03.wav`), replacing the
  synthesized placeholders — `SoundBank` picks randomly within each
  category, knock included, so more takes can be dropped in any time
- Voice sound selection (Shout/Cry/Moan checkboxes on the dashboard,
  `trigger.enabled_categories` in config) — smoke-tested including the
  "reject unchecking the last one" validation, plus live-tested in a real
  browser (found and fixed a checkbox-sync bug in the process: a checkbox
  that keeps focus after being clicked was getting skipped by the
  "don't overwrite what the user's actively editing" logic that sliders
  need, so a rejected change looked stuck instead of reverting)
- CPU temperature warning card (`monitoring.cpu_temp_warning_c`) —
  smoke-tested (edge-triggered logging, warning boolean) and live-tested
  in a real browser with the temperature reader monkeypatched to 82°C
  (this dev laptop has no thermal-zone sysfs file to read for real)
- Italian added as a third language (`vosk-model-small-it-0.22`) — model
  downloaded and smoke-tested, plus live-tested end to end: switched the
  running server to `it` via the dashboard, confirmed the listener
  rebuilt cleanly ("listener ready (language=it)") and the language
  selector reflects it on page load
- Dashboard log/status timestamps now self-correct against the Pi's
  (possibly wrong, no-RTC/no-NTP-in-AP-mode) clock — live-tested by
  faking a 3h17m server clock skew and confirming the "Clock" card
  detected exactly that offset and the log's displayed time matched
  real wall-clock time, not the skewed one
- Detection-to-response latency work (partial-result matching, smaller
  mic block size, clip preloading, low-latency audio streams) — smoke
  tested (a faked Vosk recognizer confirms the listener now reacts to a
  partial hypothesis rather than waiting for a finalized result; clip
  cache warm-up verified) and live-tested end to end (server starts,
  preloads clips, listener initializes, manual trigger still plays
  correctly) — **not yet judged by ear on real hardware with a real
  spoken keyword**, which is really the only way to feel whether the
  partial-result change actually feels snappier in practice.
- Keyword replies: a heard keyword is now answered with dedicated sounds from
  `assets/sounds/reply/`, with the `cry` sounds standing in while that folder
  is empty (and switching over by themselves once real files appear). Covered
  by tests (dedicated vs. stand-in vs. nothing available, spontaneous calls
  never using it, mode volume still applying, and the `main.py` wiring —
  checked to fail if the wiring or the fallback is broken) and run end to
  end: real spoken Italian "ciao" through the real listener/Vosk model/
  responder played the cry stand-in and, after a file was dropped into
  `reply/` mid-run, played that file instead; the dashboard card followed.
  Not yet judged by ear on the Pi.
- Language selection fix (field report: choosing Italian "had no effect"):
  two causes, both fixed. (1) The Pi never had the Italian model — it's
  gitignored so `git pull` doesn't bring it, and the README's Pi steps only
  listed en/de (now include it). (2) The dashboard hid that: the refusal
  was a tiny inline message, unlogged, and the dropdown kept *showing*
  Italian (a focused control skipped resyncing) while the listener stayed on
  German. Now: uninstalled languages are marked and disabled, a refused choice
  gets a sticky banner + log entry + the fix command, and controls resync
  right after every action. Reproduced in a real browser by hiding the
  model, fixed, re-verified; plus startup warnings for a missing configured or
  saved language, and tests (missing-model refusal, keyword-vocabulary
  check per language, every language has keywords + a download case).
  **On the Pi you still need to run `bash scripts/download_vosk_model.sh it`.**
- Dashboard: "Reset to defaults" button, cooldown slider narrowed to 0-10s,
  and a "Knock on every response" toggle (plus a "Knock chance" status
  card) — pytest + smoke-tested (the override in every mode, reset
  restoring `config.yaml`'s values and deleting the saved file) and
  live-tested in a real browser against a pre-seeded saved state: the
  buttons gave immediate feedback, Reset went back to `config.yaml`'s
  values rather than the saved ones, and a restart afterwards came up
  clean. Not yet tried on the Pi/phone.
- Dashboard settings now persist across restarts (`runtime_settings.json`,
  see "Persistent dashboard settings") — tested with a pytest suite for the
  store (roundtrip, corrupt/stale/out-of-range files, atomic writes) plus a
  smoke check driving all six endpoints then rebuilding config from the
  saved file, and live-tested by changing every setting over HTTP,
  `kill -9`-ing the app, restarting it, and confirming everything (including
  the listener's language) came back; also confirmed a truncated file just
  falls back to `config.yaml`. Not yet exercised on the Pi itself.
- Cooldown is now adjustable live from the dashboard (default lowered to
  5s), with a live numeric readout while dragging — live-tested end to
  end: dragged the slider to 12 in a real browser, confirmed
  `config.trigger.cooldown_seconds` updated and the change was logged.
- Standalone WLAN AP mode built (`network.ap_mode` config +
  `scripts/setup_wifi_ap.sh`, NetworkManager hotspot via `nmcli`) and
  smoke-tested for config parsing/dashboard status only — **not yet run for
  real on the Pi** (it needs `nmcli` and touches live network state, which
  isn't something to exercise from here; try it there and let me know how
  it goes, especially the SSH-over-WiFi disconnect behavior).
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
in `trigger.keywords.<language>`) near the mic — it should answer with a sound
from `assets/sounds/reply/` (the cry sounds until you add some), and sometimes
a knock. Switch to `distress` or `weak`
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
bash scripts/download_vosk_model.sh it
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

## Standalone WLAN (Pi as its own WiFi hotspot)

For field use with no external router available, the Pi can broadcast its
own WiFi network so a phone connects directly to reach the dashboard.

**Before you run this**: a Pi 4B has a single WiFi radio. Enabling the
hotspot disconnects any existing WiFi client connection on that radio —
**including an SSH session over WiFi**. Do this over Ethernet, a direct
console (keyboard/monitor), or be ready to immediately reconnect by joining
the new hotspot network yourself. It also sets the hotspot to autoconnect,
so it comes up in AP mode on every future boot too, not just this once.

1. Set your SSID/password under `network.ap_mode` in `config.yaml` (change
   the default password — it's committed to the repo as a placeholder):
   ```yaml
   network:
     ap_mode:
       enabled: true
       ssid: "VictimSim"
       password: "your-own-password-here"   # 8+ chars, WPA2
       interface: wlan0
   ```
2. Apply it — this is a separate, explicit step, not something the app does
   on its own:
   ```bash
   bash scripts/setup_wifi_ap.sh
   ```
   It reads the config above, warns you about the disconnect, asks for
   confirmation, then sets up a NetworkManager hotspot connection
   (`nmcli`) and brings it up.
3. On your phone: connect to the `VictimSim` WiFi network, then browse to
   the address the script prints (NetworkManager's shared-mode gateway,
   typically `http://10.42.0.1:8080`).

To revert to normal WiFi client mode (e.g. to get the Pi back online at
home for maintenance):
```bash
bash scripts/setup_wifi_ap.sh disable
```
then either reboot or `nmcli connection up <your-wifi-profile-name>` (list
profiles with `nmcli connection show`).

The dashboard's "Network" status card reflects `config.yaml`'s configured
intent (whether AP mode is turned on and its SSID) — it doesn't actively
verify the hotspot is live, since that's a one-time infrastructure step
independent of whether the app happens to be running.

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
dropping `.wav` files into `assets/sounds/<shout|cry|moan|knock|reply>/` —
any filename works, `SoundBank` picks randomly within each folder (knock
included) and avoids repeating the same clip twice in a row. `reply/` is
still empty: it's where the dedicated keyword-reply recordings go (the `cry`
sounds stand in until then — see "Keyword replies").

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
- Italian keywords (`trigger.keywords.it`) haven't been reviewed by a
  native speaker. Verified so far: every keyword word exists in the Italian
  model's vocabulary (a pytest guards this for all languages), and real
  spoken "ciao" and "nessuno" (Wikimedia Commons pronunciation clips, fed
  through the real listener) were recognized — while the same audio through
  the German model wasn't. Not verified: the multi-word phrases ("c'è
  qualcuno", "mi senti", "squadra di soccorso") and anything through the
  actual ReSpeaker in the field. Worth a real-voice check before relying on
  it.
- Real recordings only cover one or a few takes per category so far — more
  variety (and a genuinely weak/exhausted-sounding take for `weak` mode)
  would help against repetition during longer training sessions.
- Dashboard has no authentication and uses Flask's built-in dev server —
  fine for a trusted training-exercise WLAN, not for exposing beyond that.
- Dashboard log is in-memory only (last 300 events, process lifetime) —
  add persistence if you need to review a session after the Pi restarts.
- AP mode's default `config.yaml` password is a committed placeholder —
  change it before deploying, and note it's stored in plaintext in the
  repo/config file like the other settings (no secrets management here).