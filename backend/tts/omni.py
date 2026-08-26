"""一体化 TTS：通过 vLLM-omni（MiniCPM-o）端点做文本转语音（raw WAV）。

复用 llm.omni 的 OpenAI 兼容端点。音频输出格式以 vllm-omni 官方为准
（MiniCPM-o 的 talker 返回 raw WAV）。
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile

from backend.tts.base import TTSEngine

logger = logging.getLogger(__name__)


class OmniTTSEngine(TTSEngine):
    def __init__(self, base_url: str, model: str, api_key: str | None = None) -> None:
        self._base_url = base_url
        self._model = model
        self._api_key = api_key
        self._mixer_ready = False
        self._stop_requested = False

    def _ensure_mixer(self) -> None:
        if self._mixer_ready:
            return
        import pygame

        try:
            pygame.mixer.init(frequency=24000)
        except Exception:
            pass
        self._mixer_ready = True

    def stop(self) -> None:
        self._stop_requested = True
        try:
            import pygame

            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass

    async def speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._stop_requested = False
        self._ensure_mixer()
        try:
            data = await asyncio.to_thread(self._synthesize, text)
            if self._stop_requested:
                return
            await asyncio.to_thread(self._play_blocking, data)
        except Exception as e:  # noqa: BLE001
            logger.warning("Omni TTS speak failed: %s", e)

    def _synthesize(self, text: str) -> bytes:
        from openai import OpenAI

        client = OpenAI(base_url=self._base_url, api_key=self._api_key or "EMPTY")
        resp = client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": f"请用语音朗读：{text}"}],
        )
        audio = getattr(resp.choices[0].message, "audio", None)
        if audio is None:
            raise RuntimeError("vLLM-omni 未返回音频（音频输出格式见 vllm-omni 官方）")
        if isinstance(audio, bytes):
            return audio
        if isinstance(audio, dict):
            if audio.get("data"):
                return base64.b64decode(audio["data"])
            if audio.get("url"):
                raise RuntimeError("音频以 URL 返回，暂未支持（见 vllm-omni 官方）")
        raise RuntimeError("无法解析 vLLM-omni 音频输出")

    def _play_blocking(self, data: bytes) -> None:
        import pygame

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(data)
            path = f.name
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                if self._stop_requested:
                    pygame.mixer.music.stop()
                    break
                pygame.time.wait(50)
        finally:
            try:
                pygame.mixer.music.unload()
            except Exception:
                pass
            try:
                os.unlink(path)
            except Exception:
                pass
