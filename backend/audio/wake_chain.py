"""唤醒回退：主唤醒（如 omni 一体化）失败/超时，自动切到本地 sherpa KWS 保底。

sherpa-onnx KWS 纯本地、永不阻塞；omni 一体化唤醒经网络，可能挂/慢。
feed() 在主唤醒上跑，捕获异常后永久切换到本地唤醒，保证「喊得醒」。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class FallbackWakeDetector:
    def __init__(self, primary, fallback) -> None:
        self._primary = primary
        self._fallback = fallback
        self._use_primary = True

    def feed(self, pcm: bytes) -> bool:
        if self._use_primary:
            try:
                return self._primary.feed(pcm)
            except Exception as e:  # noqa: BLE001
                logger.warning("omni 唤醒失败，回退本地 sherpa 唤醒: %s", e)
                self._use_primary = False
                # 主唤醒内部可能已有部分状态；切到本地后不再回切，保证后续稳定
        return self._fallback.feed(pcm)

    def close(self) -> None:
        for d in (self._primary, self._fallback):
            c = getattr(d, "close", None)
            if c is not None:
                try:
                    c()
                except Exception:
                    pass
