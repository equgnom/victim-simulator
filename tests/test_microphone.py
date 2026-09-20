from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from victimsim import audio_hal
from victimsim import listener as listener_module
from victimsim import main as main_module
from victimsim.config import MODEL_NAMES, Config
from victimsim.listener import KeywordListener, ListenerError
from victimsim.state import SharedState

BLOCK = b"\x00\x00" * 3200  # one 0.2s mono block
REAL_SLEEP = main_module._sleep_unless_interrupted  # captured before any fixture patches it


# =====================================================================================
# Layer 1: KeywordListener against a fake microphone stream
# =====================================================================================

class FakeStream:
    """Stands in for sd.RawInputStream. Feeds `blocks` to the audio callback from a thread,
    then behaves per `then`: "silent" = just stops delivering (a stall: no error at all),
    "end" = PortAudio ends the stream by itself (device unplugged / driver error)."""

    def __init__(self, script: dict, **kwargs):
        self.script, self.callback, self.finished = script, kwargs["callback"], kwargs["finished_callback"]
        self.closed = False
        self._stop = threading.Event()
        script["stream"] = self

    def start(self):
        if self.script.get("start_error"):
            raise audio_hal.sd.PortAudioError("Device unavailable")
        threading.Thread(target=self._feed, daemon=True).start()

    def _feed(self):
        for block in self.script.get("blocks", []):
            if self._stop.wait(self.script.get("interval", 0.005)):
                return
            self.callback(block, len(block) // 2, None, None)
        if self.script.get("then") == "end":
            self.finished()

    def close(self, ignore_errors=True):
        self.closed = True
        self._stop.set()


class FakeRecognizer:
    """Partial hypothesis per block, from `texts` (last one repeats). Never 'finalizes'."""

    texts: list[str] = [""]
    seen = 0
    delay = 0.0

    def __init__(self, *_a, **_k):
        pass

    def AcceptWaveform(self, _data):
        type(self).seen += 1
        if type(self).delay:
            time.sleep(type(self).delay)
        return False

    def PartialResult(self):
        i = min(type(self).seen - 1, len(type(self).texts) - 1)
        return json.dumps({"partial": type(self).texts[i]})

    def Result(self):
        return json.dumps({"text": ""})


@pytest.fixture
def mic(monkeypatch):
    """Returns a function make(script, texts=..., keywords=...) -> (KeywordListener, script)."""
    monkeypatch.setattr(listener_module, "STALL_TIMEOUT_SECONDS", 0.4)
    monkeypatch.setattr(listener_module, "POLL_SECONDS", 0.02)
    monkeypatch.setattr(listener_module, "Model", lambda *_a, **_k: object())
    monkeypatch.setattr(listener_module, "KaldiRecognizer", FakeRecognizer)

    def make(script: dict, texts=("",), keywords=("hallo",)):
        FakeRecognizer.texts, FakeRecognizer.seen, FakeRecognizer.delay = list(texts), 0, 0.0
        monkeypatch.setattr(listener_module.sd, "RawInputStream", lambda **kw: FakeStream(script, **kw))
        return KeywordListener("unused", 16000, None, list(keywords), channels=1, block_size=3200), script

    return make


def test_keyword_is_found_in_the_partial_result_and_the_stream_is_closed(mic):
    listener, script = mic({"blocks": [BLOCK] * 5}, texts=["hal", "hallo da jemand"])
    assert listener.wait_for_keyword() == ("hallo", "hallo da jemand")
    assert script["stream"].closed


def test_on_ready_fires_once_when_audio_actually_arrives(mic):
    listener, _ = mic({"blocks": [BLOCK] * 6}, texts=["", "", "", "", "", "hallo"])
    calls = []
    listener.wait_for_keyword(on_ready=lambda: calls.append(FakeRecognizer.seen))
    assert calls == [0], "called once, before the first block was recognized"


def test_a_silently_stalled_microphone_raises_instead_of_hanging(mic):
    """The USB dropout that raises no error: audio just stops. A blocking stream.read() would wait forever."""
    listener, script = mic({"blocks": [BLOCK] * 2, "then": "silent"})
    started = time.monotonic()
    with pytest.raises(ListenerError, match="no audio"):
        listener.wait_for_keyword()
    assert time.monotonic() - started < 3.0
    assert script["stream"].closed


def test_a_stream_that_ends_by_itself_raises_after_the_audio_it_already_delivered(mic):
    listener, script = mic({"blocks": [BLOCK] * 3, "then": "end"})
    started = time.monotonic()
    with pytest.raises(ListenerError, match="ended unexpectedly"):
        listener.wait_for_keyword()
    assert time.monotonic() - started < 0.4, "detected immediately, not after the stall timeout"
    assert FakeRecognizer.seen == 3, "audio already delivered is still processed first"
    assert script["stream"].closed


def test_failing_to_open_the_microphone_raises(mic, monkeypatch):
    listener, _ = mic({})

    def broken(**_kw):
        raise audio_hal.sd.PortAudioError("Invalid number of channels")

    monkeypatch.setattr(listener_module.sd, "RawInputStream", broken)
    with pytest.raises(ListenerError, match="could not open"):
        listener.wait_for_keyword()


def test_failing_to_start_the_stream_raises_and_closes_it(mic):
    listener, script = mic({"start_error": True})
    with pytest.raises(ListenerError, match="could not open"):
        listener.wait_for_keyword()
    assert script["stream"].closed


def test_a_reload_request_wins_over_the_stall_check(mic):
    """Switching language while the mic is quiet must return promptly, not wait to time out."""
    listener, _ = mic({"blocks": [], "then": "silent"})
    reload_event = threading.Event()
    threading.Timer(0.1, reload_event.set).start()
    started = time.monotonic()
    assert listener.wait_for_keyword(reload_event=reload_event) is None
    assert time.monotonic() - started < 0.3


def test_audio_captured_while_muted_is_discarded(mic):
    """The victim's own playback must not be able to trigger itself."""
    muted = threading.Event()
    muted.set()
    listener, _ = mic({"blocks": [BLOCK] * 4, "then": "end"}, texts=["hallo"])
    with pytest.raises(ListenerError):  # ran out of audio without a match
        listener.wait_for_keyword(mute_event=muted)
    assert FakeRecognizer.seen == 0


def test_a_recognizer_that_cannot_keep_up_drops_audio_instead_of_building_latency(mic, monkeypatch):
    monkeypatch.setattr(listener_module, "MAX_QUEUED_BLOCKS", 2)
    listener, _ = mic({"blocks": [BLOCK] * 60, "interval": 0.0, "then": "end"})
    FakeRecognizer.delay = 0.02
    with pytest.raises(ListenerError):
        listener.wait_for_keyword()
    assert 0 < FakeRecognizer.seen < 60, "blocks past the small queue bound are dropped"


# =====================================================================================
# Layer 2: audio_loop supervising the listener
# =====================================================================================

class ScriptedListener:
    """Stands in for KeywordListener inside audio_loop. One script item per wait_for_keyword():
      ("keyword", kw, text)      on_ready(), then returns the keyword
      ("error", exc)             raises before any audio (mic missing / can't open)
      ("ready_then_error", exc)  on_ready(), then raises (mic worked, then died)
      ("stop",)                  ends the loop"""

    def __init__(self, env, **kwargs):
        self.env, self.kwargs, self.device = env, kwargs, "unset"
        env.listeners.append(self)

    def wait_for_keyword(self, mute_event=None, reload_event=None, on_ready=None):
        env = self.env
        env.snapshots.append((env.state.listener_ready, env.state.listener_error))
        item = env.script.pop(0)
        if item[0] == "stop":
            env.state.stop_event.set()
            return None
        if item[0] == "error":
            raise item[1]
        on_ready()
        if item[0] == "ready_then_error":
            raise item[1]
        return item[1], item[2]


class Env:
    pass


@pytest.fixture
def env(monkeypatch, tmp_path):
    e = Env()
    config = Config.load()
    config.language = "de"
    e.state = SharedState(config)
    e.script, e.listeners, e.snapshots, e.delays = [], [], [], []
    e.responded, e.refreshes = [], 0

    class FakeResponder:
        busy = threading.Event()
        output_device = "old"

        def ready(self):
            return True

        def respond(self, reason, reply=False):
            if e.respond_error and not e.responded:
                e.responded.append("failed")
                raise e.respond_error
            e.responded.append((reason, reply))

    e.respond_error = None
    e.state.responder = FakeResponder()

    for lang in ("de", "en"):
        (tmp_path / MODEL_NAMES[lang]).mkdir()
    monkeypatch.setattr(main_module, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(main_module, "KeywordListener", lambda **kw: ScriptedListener(e, **kw))
    e.resolve_results = []  # per-call: an Exception to raise, or a value to return

    def resolve(name, kind):
        if kind == "input" and e.resolve_results:
            r = e.resolve_results.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return 7 if kind == "output" else 3

    def refresh(after=None):
        e.refreshes += 1
        if after:
            after()

    monkeypatch.setattr(main_module.audio_hal, "resolve_device", resolve)
    monkeypatch.setattr(main_module.audio_hal, "refresh_devices", refresh)
    monkeypatch.setattr(main_module, "_sleep_unless_interrupted", lambda state, seconds: e.delays.append(seconds))
    return e


def logs(env: Env) -> list[str]:
    return [entry.message for entry in env.state.snapshot_log()]


def test_the_keyword_path_asks_for_a_reply(env):
    """The wiring: audio_loop must call respond(..., reply=True) when a keyword is heard."""
    env.script = [("keyword", "hallo", "hallo da"), ("stop",)]
    main_module.audio_loop(env.state)
    assert env.responded == [("heard 'hallo da' (matched 'hallo')", True)]
    assert "listener ready (language=de)" in logs(env)


def test_a_microphone_that_dies_mid_session_is_reported_retried_and_recovers(env):
    env.script = [
        ("keyword", "hallo", "hallo"),
        ("ready_then_error", ListenerError("the microphone stream ended unexpectedly")),
        ("keyword", "hallo", "hallo again"),
        ("stop",),
    ]
    main_module.audio_loop(env.state)

    assert len(env.responded) == 2, "keeps working after the outage"
    assert env.snapshots[2] == (False, "the microphone stream ended unexpectedly"), \
        "during the outage the dashboard sees 'not ready' and why"
    lost = [m for m in logs(env) if "MICROPHONE UNAVAILABLE" in m]
    assert len(lost) == 1 and "unexpectedly" in lost[0]
    assert any("microphone is back" in m for m in logs(env))
    # snapshots[3] is what the state looked like when the loop was told to stop
    # (after that, audio_loop itself marks the listener not-ready, by design)
    assert env.snapshots[3] == (True, None), "listening again, error cleared"
    assert env.refreshes >= 1, "PortAudio's device list must be re-scanned, or a replugged mic stays invisible"


def test_a_missing_microphone_at_startup_does_not_stop_the_app(env):
    env.resolve_results = [audio_hal.DeviceNotFoundError("No input device matching 'ReSpeaker' found."),
                           audio_hal.DeviceNotFoundError("No input device matching 'ReSpeaker' found.")]
    env.script = [("keyword", "hallo", "hallo"), ("stop",)]
    main_module.audio_loop(env.state)  # returns normally instead of raising

    assert env.responded, "once the device shows up it just starts working"
    assert env.snapshots[0] == (False, "No input device matching 'ReSpeaker' found.")
    assert [m for m in logs(env) if "MICROPHONE UNAVAILABLE" in m]


def test_the_same_failure_repeating_is_logged_once_not_every_retry(env):
    same = ListenerError("no audio from the microphone for 5s (device stalled?)")
    env.script = [("error", same)] * 4 + [("keyword", "hallo", "hallo"), ("stop",)]
    main_module.audio_loop(env.state)
    assert len([m for m in logs(env) if "unavailable" in m.lower()]) == 1


def test_a_different_failure_is_logged_when_the_reason_changes(env):
    env.script = [("error", ListenerError("could not open the microphone: busy")),
                  ("error", ListenerError("no audio for 5s")),
                  ("stop",)]
    main_module.audio_loop(env.state)
    assert any("still unavailable: no audio for 5s" in m for m in logs(env))


def test_retries_back_off_and_reset_after_a_success(env):
    boom = ListenerError("x")
    env.script = [("error", boom)] * 3 + [("keyword", "hallo", "hallo"), ("error", boom), ("stop",)]
    main_module.audio_loop(env.state)
    assert env.delays == [1.0, 2.0, 4.0, 1.0], "doubling, then back to the minimum once the mic worked again"


def test_backoff_is_capped(env):
    env.script = [("error", ListenerError("x"))] * 8 + [("stop",)]
    main_module.audio_loop(env.state)
    assert max(env.delays) == main_module.MIC_RETRY_MAX_SECONDS


def test_an_unexpected_bug_does_not_silently_end_listening(env):
    env.script = [("error", ValueError("boom")), ("keyword", "hallo", "hallo"), ("stop",)]
    main_module.audio_loop(env.state)
    assert env.responded
    assert any("unexpected ValueError: boom" in m for m in logs(env))


def test_a_failing_response_does_not_stop_listening(env):
    """e.g. someone emptied a sound folder — that's a reply problem, not a reason to stop hearing keywords."""
    env.respond_error = FileNotFoundError("No .wav clips in assets/sounds/cry")
    env.script = [("keyword", "hallo", "one"), ("keyword", "hallo", "two"), ("stop",)]
    main_module.audio_loop(env.state)
    assert env.responded[-1] == ("heard 'two' (matched 'hallo')", True)
    assert any("response failed: FileNotFoundError" in m for m in logs(env))


def test_the_output_device_index_is_re_resolved_after_the_devices_are_re_scanned(env):
    env.script = [("error", ListenerError("x")), ("stop",)]
    main_module.audio_loop(env.state)
    assert env.state.responder.output_device == 7 == env.state.output_device


def test_shutdown_during_an_outage_is_prompt(env, monkeypatch):
    """Even with a long backoff, stopping must not wait for it to elapse."""
    monkeypatch.setattr(main_module, "_sleep_unless_interrupted", REAL_SLEEP)
    monkeypatch.setattr(main_module, "MIC_RETRY_MIN_SECONDS", 30.0)
    monkeypatch.setattr(main_module, "MIC_RETRY_MAX_SECONDS", 30.0)
    env.script = [("error", ListenerError("x"))] * 5
    threading.Timer(0.3, env.state.stop_event.set).start()
    started = time.monotonic()
    main_module.audio_loop(env.state)
    assert time.monotonic() - started < 2.0


def test_switching_language_during_an_outage_rebuilds_the_listener(env, monkeypatch):
    def dashboard_switches_language(state, seconds):
        env.delays.append(seconds)
        env.state.language = "en"
        env.state.reload_event.set()

    monkeypatch.setattr(main_module, "_sleep_unless_interrupted", dashboard_switches_language)
    env.script = [("error", ListenerError("x")), ("stop",)]
    main_module.audio_loop(env.state)

    assert len(env.listeners) == 2, "a fresh listener for the new language"
    assert env.listeners[0].kwargs["keywords"] != env.listeners[1].kwargs["keywords"]


# =====================================================================================
# Re-scanning devices must not collide with playback
# =====================================================================================

def test_refresh_devices_waits_for_playback_and_runs_the_callback_inside_the_lock(monkeypatch):
    calls = []
    monkeypatch.setattr(audio_hal.sd, "_terminate", lambda: calls.append("terminate"))
    monkeypatch.setattr(audio_hal.sd, "_initialize", lambda: calls.append("initialize"))

    audio_hal.audio_lock.acquire()  # "a clip is playing"
    done = threading.Event()

    def refresh():
        audio_hal.refresh_devices(after=lambda: calls.append(("after", audio_hal.audio_lock.locked())))
        done.set()

    threading.Thread(target=refresh, daemon=True).start()
    time.sleep(0.2)
    assert calls == [] and not done.is_set(), "must not re-initialize PortAudio under a playing stream"
    audio_hal.audio_lock.release()
    assert done.wait(2.0)
    assert calls == ["terminate", "initialize", ("after", True)]
