"""TTS 抽象：文本转语音并播放。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class TTSEngine(ABC):
    _last_error: str | None = None  # speak 内部吞掉的最后一次错误（试听端点据实回报用）

    # synthesize() 产出的音频容器后缀（试听缓存文件命名用）；默认 mp3，wav 系引擎覆盖
    audio_ext: str = ".mp3"

    @abstractmethod
    async def speak(self, text: str) -> None:
        """合成并播放文本，直到播完返回。"""

    async def synthesize(self, text: str) -> bytes:
        """只合成不播放：返回完整音频字节，供试听端点落盘缓存、前端 <audio> 播放。

        失败直接抛异常（试听端点捕获后如实回报），与 speak 的吞错策略不同——
        试听场景没有主流程要保护。默认不支持，由各引擎按产出格式覆盖。
        """
        raise NotImplementedError(f"{type(self).__name__} 不支持纯合成试听")

    def cache_fingerprint(self) -> str:
        """引擎配置指纹（provider/音色/语速/档位/模型），与文本一起构成试听缓存键。"""
        return type(self).__name__

    def preflight(self) -> str | None:
        """合成前自检：返回人能看懂的问题提示；None 表示可以合成。"""
        return None
