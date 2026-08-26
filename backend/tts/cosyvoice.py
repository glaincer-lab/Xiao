"""TTS：阿里云 CosyVoice（付费云，DashScope 非实时语音合成）+ pygame 播放。

走 dashscope 的 SpeechSynthesizer（模型 cosyvoice-v1），合成 MP3 后用 pygame 播放。
API Key 复用阿里云百炼的 DASHSCOPE_API_KEY（与 ASR 同一把 key）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile

from backend.tts.base import TTSEngine

logger = logging.getLogger(__name__)


class CosyVoiceEngine(TTSEngine):
    """阿里云 CosyVoice 付费云语音合成。"""

    def __init__(self, voice: str = "longxiaochun", api_key: str | None = None) -> None:
        self._voice = voice
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
        self._ensure_mixer()
        try:
            data = await asyncio.to_thread(self._synthesize, text)
            if self._stop_requested:
                return
            await asyncio.to_thread(self._play_blocking, data)
        except Exception as e:  # noqa: BLE001
            # 合成/播放失败不应中断主流程（文字回复仍显示在界面）
            logger.warning("CosyVoice speak failed: %s", e)

    def _synthesize(self, text: str) -> bytes:
        import dashscope
        from dashscope.audio.tts import SpeechSynthesizer

        if self._api_key:
            dashscope.api_key = self._api_key  # 留空则用环境变量 DASHSCOPE_API_KEY
        result = SpeechSynthesizer.call(
            model="cosyvoice-v1",
            text=text,
            voice=self._voice,
            format="mp3",
        )
        data = result.get_audio_data()
        if not data:
            raise RuntimeError(f"CosyVoice 合成失败：{result.get_response()}")
        return data

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
