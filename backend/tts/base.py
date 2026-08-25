"""TTS 抽象：文本转语音并播放。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class TTSEngine(ABC):
    @abstractmethod
    async def speak(self, text: str) -> None:
        """合成并播放文本，直到播完返回。"""
