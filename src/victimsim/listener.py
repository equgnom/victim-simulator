"""Offline keyword spotting on the mic input, using Vosk.

Listens continuously and returns as soon as recognized speech contains one
of the configured trigger keywords (e.g. "hello", "anyone there").
"""

from __future__ import annotations

import json
import queue
import threading
import time

import numpy as np
import sounddevice as sd
from vosk import KaldiRecognizer, Model, SetLogLevel

SetLogLevel(-1)  # silence Vosk's C++ logging

# No audio for this long = the microphone has stalled (a USB dropout that raises
# no error). Blocks arrive every ~0.2s, so this is very generous.
STALL_TIMEOUT_SECONDS = 5.0
# How often the wait wakes up to check for a reload request / stall / dead stream.
POLL_SECONDS = 0.25
# If the recognizer can't keep up, drop audio rather than let latency build up
# (50 blocks ~ 10s of audio).
MAX_QUEUED_BLOCKS = 50


class ListenerError(RuntimeError):
    """The microphone failed to open, stopped, or stopped delivering audio. The
    caller (audio_loop) logs it and retries — it must not kill the listener."""


class KeywordListener:
    def __init__(
        self,
        model_path,
        samplerate: int,
        device: int | None,
        keywords: list[str],
        channels: int = 1,
        block_size: int = 3200,
    ):
        self.model = Model(str(model_path))
        self.samplerate = samplerate
        self.device = device
        self.channels = channels
        self.keywords = [k.lower() for k in keywords]
        self.block_size = block_size

    def _matches(self, text: str) -> str | None:
        text = text.lower()
        for kw in self.keywords:
            if kw in text:
                return kw
        return None

    def _to_mono_bytes(self, raw: bytes) -> bytes:
        if self.channels == 1:
            return raw
        frames = np.frombuffer(raw, dtype=np.int16).reshape(-1, self.channels)
        mono = frames.mean(axis=1).astype(np.int16)
        return mono.tobytes()

    def wait_for_keyword(self, mute_event=None, reload_event=None, on_ready=None) -> tuple[str, str] | None:
        """Blocks until a keyword is heard; returns (matched_keyword, full_text).

        Checks Vosk's streaming *partial* hypothesis on every block, not
        just the finalized result after it detects end-of-utterance silence
        — waiting for that endpoint is the single biggest source of
        detection latency, often several hundred ms to over a second, so
        this reacts as soon as a keyword shows up in the in-progress guess
        instead. Trade-off: a partial hypothesis can still change before
        Vosk settles on it, so this can occasionally trigger a beat early
        on text that isn't quite final — acceptable here since an extra or
        early trigger just means one more response, not a wrong reading.

        If `mute_event` is set (a threading.Event), audio is still consumed to keep
        the stream alive, but discarded while it's set — used to mute listening
        while the victim's own response is playing, so it doesn't re-trigger on
        itself.

        If `reload_event` is set (e.g. the web dashboard switched language),
        returns None so the caller can rebuild the listener with the new
        language's model/keywords.

        `on_ready()` is called once, when the first audio block has actually
        arrived — "the mic is really delivering", not merely "the stream opened".

        Raises ListenerError if the microphone can't be opened, ends its stream on
        its own (unplugged / driver error), or goes quiet for STALL_TIMEOUT_SECONDS.
        Audio arrives through a callback into a queue rather than blocking
        stream.read() calls, because a blocking read has no timeout: a mic that
        stalls without an error would hang this thread forever while the dashboard
        kept saying "listening".
        """
        recognizer = KaldiRecognizer(self.model, self.samplerate)
        blocks: queue.Queue[bytes] = queue.Queue(maxsize=MAX_QUEUED_BLOCKS)
        stream_ended = threading.Event()  # PortAudio ended the stream by itself

        def on_audio(indata, frames, time_info, status):
            try:
                blocks.put_nowait(bytes(indata))
            except queue.Full:
                pass  # not keeping up: drop this block instead of lagging further behind

        stream = None
        try:
            stream = sd.RawInputStream(
                samplerate=self.samplerate,
                blocksize=self.block_size,
                device=self.device,
                dtype="int16",
                channels=self.channels,
                latency="low",
                callback=on_audio,
                finished_callback=stream_ended.set,
            )
            stream.start()  # raises PortAudioError if the device can't be started
        except sd.PortAudioError as e:
            if stream is not None:
                stream.close()
            raise ListenerError(f"could not open the microphone: {e}") from e

        try:
            last_audio = time.monotonic()
            announced = False
            while True:
                if reload_event is not None and reload_event.is_set():
                    return None
                try:
                    data = blocks.get(timeout=POLL_SECONDS)
                except queue.Empty:
                    if stream_ended.is_set():
                        raise ListenerError(
                            "the microphone stream ended unexpectedly (device unplugged or driver error)"
                        )
                    if time.monotonic() - last_audio > STALL_TIMEOUT_SECONDS:
                        raise ListenerError(
                            f"no audio from the microphone for {STALL_TIMEOUT_SECONDS:.0f}s (device stalled?)"
                        )
                    continue

                last_audio = time.monotonic()
                if not announced:
                    announced = True
                    if on_ready is not None:
                        on_ready()
                if mute_event is not None and mute_event.is_set():
                    continue
                mono = self._to_mono_bytes(data)
                if recognizer.AcceptWaveform(mono):
                    result = json.loads(recognizer.Result())
                    text = result.get("text", "")
                    if not text:
                        continue
                    kw = self._matches(text)
                    if kw:
                        return kw, text
                else:
                    partial = json.loads(recognizer.PartialResult())
                    text = partial.get("partial", "")
                    if not text:
                        continue
                    kw = self._matches(text)
                    if kw:
                        return kw, text
        finally:
            stream.close()  # ignore_errors defaults to True: a dead device mustn't turn this into a second error
