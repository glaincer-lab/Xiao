"""TTS：本地 Piper（完全离线）+ pygame 播放。

依赖：pip install piper-tts（含 onnxruntime + espeak-ng-data）。
声库：从 rhasspy/piper-voices 下载中文 .onnx（如 zh_CN-huayan-medium.onnx），
在设置面板填该文件的本地路径。API 以 piper-tts 官方为准（PiperVoice.load + synthesize）。
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
import wave

from backend.tts.base import TTSEngine

logger = logging.getLogger(__name__)


class PiperEngine(TTSEngine):
    """本地 Piper 语音合成。"""

    def __init__(self, model_path: str = "") -> None:
        self._model_path = model_path
        self._voice = None
        self._mixer_ready = False
        self._stop_requested = False

    def _get_voice(self):
        if self._voice is None:
            from piper import PiperVoice

            if not self._model_path:
                raise RuntimeError("未配置 Piper 声库路径（.onnx）")
            self._voice = PiperVoice.load(self._model_path)
        return self._voice

    def _ensure_mixer(self) -> None:
        if self._mixer_ready:
            return
        import pygame

        try:
            pygame.mixer.init(frequency=22050)
        except Exception:
            pass  # 无音频设备时容错
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
            logger.warning("Piper speak failed: %s", e)

    def _synthesize(self, text: str) -> bytes:
        voice = self._get_voice()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            voice.synthesize(text, wav_file)
        return buf.getvalue()

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
