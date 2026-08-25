"""ASR 抽象：流式喂入 PCM，通过回调上抛中间/最终结果。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

# on_result(is_final: bool, text: str)
ResultCallback = Callable[[bool, str], None]


class ASREngine(ABC):
    def __init__(self, on_result: ResultCallback) -> None:
        self.on_result = on_result

    @abstractmethod
    def start(self) -> None:
        """开始一段新的识别。"""

    @abstractmethod
    def feed(self, pcm: bytes) -> None:
        """喂入 16kHz / 16bit / mono 的 PCM 音频块。"""

    @abstractmethod
    def stop(self) -> str:
        """结束本次识别，返回最终文本。"""

    def close(self) -> None:
        """释放资源，默认无操作。"""
