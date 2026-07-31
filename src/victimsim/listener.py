"""Offline keyword spotting on the mic input, using Vosk.

Listens continuously and returns as soon as recognized speech contains one
of the configured trigger keywords (e.g. "hello", "anyone there").
"""

from __future__ import annotations

import json

import numpy as np
import sounddevice as sd
from vosk import KaldiRecognizer, Model, SetLogLevel

SetLogLevel(-1)  # silence Vosk's C++ logging


class KeywordListener:
    def __init__(
        self,
        model_path,
        samplerate: int,
        device: int | None,
        keywords: list[str],
        channels: int = 1,
        block_size: int = 8000,
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

    def wait_for_keyword(self, mute_event=None) -> tuple[str, str]:
        """Blocks until a keyword is heard; returns (matched_keyword, full_text).

        If `mute_event` is set (a threading.Event), audio is still read to keep
        the stream alive, but discarded while it's set — used to mute listening
        while the victim's own response is playing, so it doesn't re-trigger on
        itself.
        """
        recognizer = KaldiRecognizer(self.model, self.samplerate)
        with sd.RawInputStream(
            samplerate=self.samplerate,
            blocksize=self.block_size,
            device=self.device,
            dtype="int16",
            channels=self.channels,
        ) as stream:
            while True:
                data, _overflow = stream.read(self.block_size)
                if mute_event is not None and mute_event.is_set():
                    continue
                mono = self._to_mono_bytes(bytes(data))
                if recognizer.AcceptWaveform(mono):
                    result = json.loads(recognizer.Result())
                    text = result.get("text", "")
                    if not text:
                        continue
                    kw = self._matches(text)
                    if kw:
                        return kw, text
