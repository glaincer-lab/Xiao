"""ASR 真回退链：主引擎识别，超时/异常时用已缓存的音频喂本地兜底引擎。

主引擎（云 / omni）超时或抛错时，不放弃本次识别，而是把本段缓存的 PCM
交给本地 FunASR 兜底重识别，保证「能识别出来」，而不是这次直接失败。

start/feed 在主引擎上跑；仅当 stop() 失败/超时才回退。本地引擎（FunASR）
离线运行，不会网络阻塞。
"""
from __future__ import annotations

import concurrent.futures
import logging

from backend.asr.base import ASREngine

logger = logging.getLogger(__name__)


class ASRChain(ASREngine):
    def __init__(self, primary: ASREngine, fallback: ASREngine | None = None, timeout: float = 5.0) -> None:
        super().__init__(None)  # on_result 由各引擎自行持有
        self._primary = primary
        self._fallback = fallback
        self._timeout = timeout
        self._buf = bytearray()
        self._started = False
        # 单线程执行器：stop() 用 future + timeout 兜底，防止任何主引擎同步阻塞卡死音频线程
        self._exec = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def start(self) -> None:
        self._buf = bytearray()
        self._started = True
        self._primary.start()

    def feed(self, pcm: bytes) -> None:
        if not self._started:
            return
        self._buf += pcm
        try:
            self._primary.feed(pcm)
        except Exception:
            pass

    def _transcribe(self, engine, data: bytes) -> str:
        engine.start()
        if data:
            engine.feed(data)
        return engine.stop() or ""

    def stop(self) -> str:
        if not self._started:
            return ""
        self._started = False
        # 主引擎 stop() 也经 executor + timeout，保证任何同步阻塞都在 _timeout 内让出音频线程
        try:
            fut = self._exec.submit(self._primary.stop)
            text = fut.result(timeout=self._timeout) or ""
            if text.strip():
                return text
            logger.warning("主 ASR 返回空文本，尝试本地兜底")
        except concurrent.futures.TimeoutError:
            logger.warning("主 ASR 识别超时，回退本地引擎")
        except Exception as e:  # noqa: BLE001
            logger.warning("主 ASR 识别异常，回退本地引擎: %s", e)
        return self._fallback_transcribe()

    def _fallback_transcribe(self) -> str:
        if self._fallback is None:
            return ""
        try:
            fut = self._exec.submit(self._transcribe, self._fallback, bytes(self._buf))
            return fut.result(timeout=self._timeout) or ""
        except Exception as e:  # noqa: BLE001
            logger.error("本地 ASR 回退也失败: %s", e)
            return ""

    def close(self) -> None:
        for e in (self._primary, self._fallback):
            if e is None:
                continue
            c = getattr(e, "close", None)
            if c is not None:
                try:
                    c()
                except Exception:
                    pass
        self._exec.shutdown(wait=False)
