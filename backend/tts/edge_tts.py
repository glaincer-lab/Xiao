"""TTS：edge-tts（免费、中文自然）+ pygame 播放。

流式优化：把文本按句切分，第一句合成完立即开播，后续句子边播边预合成，
让「开始说话」尽量和前端打字同步，而不是等整段合成完。
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import tempfile

from backend.tts.base import TTSEngine

logger = logging.getLogger(__name__)

MIN_CHUNK = 20  # 每个播报片段的最短字数（首句越小，开播越快）


class EdgeTTS(TTSEngine):
    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural", rate: str = "+0%") -> None:
        self._voice = voice
        self._rate = rate
        self._mixer_ready = False
        self._stop_requested = False

    def _ensure_mixer(self) -> None:
        if self._mixer_ready:
            return
        import pygame

        try:
            pygame.mixer.init(frequency=24000)
        except Exception:
            pass  # 无音频设备时容错
        self._mixer_ready = True

    def stop(self) -> None:
        """立刻停止当前播报（打断用）。"""
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
        chunks = self._split(text)
        self._ensure_mixer()
        try:
            # 第一句：合成完立即开播（后台线程），同时预合成后续句子
            first = await self._synthesize(chunks[0])
            if self._stop_requested:
                return
            rest_task = asyncio.create_task(self._synthesize_all(chunks[1:]))
            await asyncio.to_thread(self._play_blocking, first)
            rest = await rest_task
            for data in rest:
                if self._stop_requested:
                    break
                await asyncio.to_thread(self._play_blocking, data)
        except Exception as e:  # noqa: BLE001
            # 合成/播放失败不应中断主流程（文字回复仍显示在界面）
            logger.warning("TTS speak failed: %s", e)

    async def _synthesize(self, text: str) -> bytes:
        import edge_tts

        communicate = edge_tts.Communicate(text, self._voice, rate=self._rate)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        return buf.getvalue()

    async def _synthesize_all(self, chunks: list[str]) -> list[bytes]:
        return [await self._synthesize(c) for c in chunks]

    def _play_blocking(self, data: bytes) -> None:
        import pygame

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
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

    @staticmethod
    def _split(text: str) -> list[str]:
        """按标点/换行切分成播报片段，避免整段合成造成的开播延迟。"""
        parts = re.split(r"(?<=[。！？!?；;\n])", text)
        chunks: list[str] = []
        buf = ""
        for p in parts:
            if not p:
                continue
            buf += p
            if len(buf) >= MIN_CHUNK:
                chunks.append(buf)
                buf = ""
        if buf:
            chunks.append(buf)
        return chunks or [text]
