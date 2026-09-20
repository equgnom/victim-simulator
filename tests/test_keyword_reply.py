from __future__ import annotations

import threading
import wave
from pathlib import Path

import pytest

from victimsim import audio_hal
from victimsim import main as main_module
from victimsim.config import MODEL_NAMES, Config
from victimsim.responder import Responder
from victimsim.sound_bank import SoundBank
from victimsim.state import SharedState


def _wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00\x00" * 160)
    return path


class RecordingBank(SoundBank):
    """Remembers which clip was picked, so tests can see where a sound came from."""

    def __init__(self, sounds_dir: Path):
        super().__init__(sounds_dir)
        self.picks: list[Path] = []

    def pick(self, category: str) -> Path:
        path = super().pick(category)
        self.picks.append(path)
        return path

    @property
    def picked_categories(self) -> list[str]:
        return [p.parent.name for p in self.picks]


@pytest.fixture
def played(monkeypatch):
    """No real audio: record what would have been played, and at what voice volume."""
    calls = {"play": 0, "mix": []}
    monkeypatch.setattr(audio_hal, "play_blocking", lambda *a, **k: calls.__setitem__("play", calls["play"] + 1))
    real_mix = audio_hal.mix_to_stereo

    def spy(*args):
        calls["mix"].append(args)
        return real_mix(*args)

    monkeypatch.setattr(audio_hal, "mix_to_stereo", spy)
    return calls


@pytest.fixture
def config() -> Config:
    cfg = Config.load()
    cfg.knock.enabled = False  # keep knock picks out of the way unless a test wants them
    return cfg


def _bank(tmp_path: Path, with_reply: bool, with_cry: bool = True) -> RecordingBank:
    for category in ("shout", "moan", "knock"):
        _wav(tmp_path / category / "a.wav")
    if with_cry:
        _wav(tmp_path / "cry" / "cry.wav")
    if with_reply:
        _wav(tmp_path / "reply" / "r1.wav")
        _wav(tmp_path / "reply" / "r2.wav")
    return RecordingBank(tmp_path)


def test_reply_plays_from_the_dedicated_folder_when_it_has_files(config, tmp_path, played):
    bank = _bank(tmp_path, with_reply=True)
    responder = Responder(config, bank, None)
    for _ in range(10):
        responder._last_response_at = 0.0
        responder.respond("heard 'hallo'", reply=True)
    assert set(bank.picked_categories) == {"reply"}


def test_reply_falls_back_to_cry_while_the_folder_is_empty(config, tmp_path, played):
    bank = _bank(tmp_path, with_reply=False)
    state = SharedState(config)
    responder = Responder(config, bank, None, state=state)
    responder.respond("heard 'hallo'", reply=True)

    assert bank.picked_categories == ["cry"]
    assert played["play"] == 1, "a keyword must never be answered with silence"
    assert "stand-in" in state.last_response["category"]
    assert any("stand-in" in e.message for e in state.snapshot_log()), "the log should say a stand-in was used"


def test_real_files_take_over_without_a_restart_and_leave_nothing_to_delete(config, tmp_path, played):
    bank = _bank(tmp_path, with_reply=False)
    responder = Responder(config, bank, None)
    assert responder.reply_info() == {"category": "reply", "clips": 0, "placeholder": "cry"}

    _wav(tmp_path / "reply" / "real.wav")  # dropped in while the simulator is running
    assert responder.reply_info() == {"category": "reply", "clips": 1, "placeholder": None}
    responder.respond("heard 'hallo'", reply=True)
    assert bank.picked_categories == ["reply"]


def test_reply_falls_back_to_a_normal_pick_rather_than_crash_if_nothing_is_available(config, tmp_path, played):
    bank = _bank(tmp_path, with_reply=False, with_cry=False)  # no reply/, no cry/
    config.trigger.enabled_categories = ["shout", "moan"]  # the normal pick must only see folders that exist
    responder = Responder(config, bank, None)
    responder.respond("heard 'hallo'", reply=True)
    assert bank.picked_categories[0] in {"shout", "moan"}
    assert played["play"] == 1
    assert responder.reply_info()["placeholder"] is None


def test_calls_that_are_not_keyword_replies_never_use_the_reply_sounds(config, tmp_path, played):
    """Spontaneous calls and the manual 'Trigger now' keep using shout/cry/moan."""
    bank = _bank(tmp_path, with_reply=True)
    responder = Responder(config, bank, None)
    for _ in range(40):
        responder._last_response_at = 0.0
        responder.respond("spontaneous call")
    assert "reply" not in bank.picked_categories
    assert set(bank.picked_categories) <= {"shout", "cry", "moan"}


def test_reply_ignores_the_voice_category_checkboxes_and_mode_category_list(config, tmp_path, played):
    """Those choose among spontaneous-call sounds; a reply has its own dedicated set."""
    bank = _bank(tmp_path, with_reply=True)
    config.trigger.enabled_categories = ["moan"]
    config.behavior.mode = "weak"
    config.behavior.profiles["weak"].response_categories = ["moan"]
    Responder(config, bank, None).respond("heard 'hallo'", reply=True)
    assert bank.picked_categories == ["reply"]


def test_reply_volume_still_follows_the_mode(config, tmp_path, played):
    """A weak victim answers quietly, like it does everything else."""
    bank = _bank(tmp_path, with_reply=True)
    responder = Responder(config, bank, None)

    config.behavior.mode = "responsive"
    responder.respond("heard 'hallo'", reply=True)
    normal = played["mix"][-1][4]

    responder._last_response_at = 0.0
    config.behavior.mode = "weak"
    responder.respond("heard 'hallo'", reply=True)
    weak = played["mix"][-1][4]

    assert normal == pytest.approx(config.volume.voice)
    assert weak == pytest.approx(config.volume.voice * config.behavior.profiles["weak"].volume_multiplier)
    assert weak < normal


def test_a_reply_can_still_be_accompanied_by_a_knock(config, tmp_path, played):
    config.knock.enabled = True
    config.knock.probability_override = 1.0
    bank = _bank(tmp_path, with_reply=True)
    Responder(config, bank, None).respond("heard 'hallo'", reply=True)
    assert sorted(bank.picked_categories) == ["knock", "reply"]


def test_the_keyword_path_asks_for_a_reply(monkeypatch, tmp_path):
    """The wiring: audio_loop must call respond(..., reply=True) when a keyword is heard."""
    config = Config.load()
    config.language = "de"
    state = SharedState(config)
    calls: list[tuple[str, bool]] = []

    class FakeResponder:
        busy = threading.Event()

        def ready(self) -> bool:
            return True

        def respond(self, reason: str, reply: bool = False) -> None:
            calls.append((reason, reply))

    class FakeListener:
        def __init__(self, **_kwargs):
            self.calls = 0

        def wait_for_keyword(self, mute_event=None, reload_event=None):
            self.calls += 1
            if self.calls == 1:
                return "hallo", "hallo da"
            state.stop_event.set()
            return None

    state.responder = FakeResponder()
    (tmp_path / MODEL_NAMES["de"]).mkdir()
    monkeypatch.setattr(main_module, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(main_module, "KeywordListener", FakeListener)

    main_module.audio_loop(state, None, None)

    assert calls == [("heard 'hallo da' (matched 'hallo')", True)]
