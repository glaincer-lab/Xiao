"""一体化 ASR：通过 vLLM-omni（MiniCPM-o）端点做音频转文本。

复用 llm.omni 的 OpenAI 兼容端点（base_url/model）：把 PCM 转 WAV 后以
audio_url（base64 data URI）发给 chat completions，返回文本。
"""
from __future__ import annotations

import base64
import io
import wave

from backend.asr.base import ASREngine, ResultCallback


class OmniASREngine(ASREngine):
    def __init__(self, on_result: ResultCallback, base_url: str, model: str, api_key: str | None = None) -> None:
        super().__init__(on_result)
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key or "EMPTY")
        self._model = model
        self._buf = bytearray()

    def start(self) -> None:
        self._buf = bytearray()

    def feed(self, pcm: bytes) -> None:
        self._buf += pcm

    def stop(self) -> str:
        if not self._buf:
            return ""
        wav = self._pcm_to_wav(bytes(self._buf))
        b64 = base64.b64encode(wav).decode()
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "请把这段音频转写成文字。"},
                    {"type": "audio_url", "audio_url": {"url": f"data:audio/wav;base64,{b64}"}},
                ],
            }],
        )
        text = (resp.choices[0].message.content or "").strip()
        if text:
            self.on_result(True, text)
        return text

    @staticmethod
    def _pcm_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm)
        return buf.getvalue()
