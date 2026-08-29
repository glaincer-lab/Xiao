"""TTS 真回退链：按顺序合成+播放，某引擎超时/失败自动降级到下一个。

顺序 = [当前选定引擎, 免费云兜底 edge-tts, 本地保底 Piper]。
目的：任一云引擎挂起/超时不再「这次播报失败」，而是逐层退回能出声的引擎，
符合产品愿景「云 → edge-tts → Piper(本地)」；若本地面也未配置，则记录后返回，
主对话流水线不阻塞（不堵死 speak 调用链）。

speak() 内部吞错（与各引擎一致），失败记入 _last_error 供试听端点据实回报。
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
import os

from backend.tts.base import TTSEngine

logger = logging.getLogger(__name__)


class TTSChain(TTSEngine):
    def __init__(self, engines: list[TTSEngine], timeout: float = 10.0) -> None:
        self._engines = [e for e in engines if e is not None]
        self._timeout = timeout
        self._stop_requested = False
        self._last_error: str | None = None
        self._mixer_ready = False

    def _ensure_mixer(self) -> None:
        if self._mixer_ready:
            return
        try:
            import pygame

            if not pygame.mixer.get_init():
                pygame.mixer.init()
        except Exception:
            pass
        self._mixer_ready = True

    def _play(self, data: bytes) -> None:
        try:
            import pygame

            self._ensure_mixer()
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
        except Exception:
            pass

    def stop(self) -> None:
        self._stop_requested = True
        for e in self._engines:
            s = getattr(e, "stop", None)
            if s is not None:
                try:
                    s()
                except Exception:
                    pass

    async def speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._stop_requested = False
        last: str | None = None
        for eng in self._engines:
            if self._stop_requested:
                return
            try:
                audio = await asyncio.wait_for(eng.synthesize(text), timeout=self._timeout)
                if not audio:
                    raise RuntimeError("空音频")
                if self._stop_requested:
                    return
                await asyncio.to_thread(self._play, audio)
                return
            except asyncio.TimeoutError:
                logger.warning("TTS %s 合成超时，回退下一引擎", type(eng).__name__)
                last = f"{type(eng).__name__} 超时"
            except Exception as e:  # noqa: BLE001
                logger.warning("TTS %s 合成失败，回退下一引擎: %s", type(eng).__name__, e)
                last = str(e)
        self._last_error = last or "所有 TTS 引擎均不可用"
        logger.warning("TTS 回退链全部失败: %s", self._last_error)

    async def synthesize(self, text: str) -> bytes:
        """试听：用第一个能合成的引擎返回整段音频（失败抛异常，供试听端点如实回报）。"""
        text = (text or "").strip()
        if not text:
            return b""
        last: str | None = None
        for eng in self._engines:
            try:
                return await asyncio.wait_for(eng.synthesize(text), timeout=self._timeout)
            except asyncio.TimeoutError:
                last = f"{type(eng).__name__} 超时"
                continue
            except Exception as e:  # noqa: BLE001
                last = str(e)
                continue
        raise RuntimeError(f"TTS 回退链合成失败: {last}")

    def cache_fingerprint(self) -> str:
        if self._engines:
            return getattr(self._engines[0], "cache_fingerprint", lambda: "tts-chain")()
        return "tts-chain"
