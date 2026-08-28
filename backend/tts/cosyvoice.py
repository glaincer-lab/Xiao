"""TTS：阿里云付费云语音合成（CosyVoice v3 / Qwen-Audio-TTS）+ pygame 播放。

走 dashscope 的 tts_v2 接口（dashscope.audio.tts_v2），合成 MP3 后用 pygame 播放。
API Key 复用阿里云百炼的 DASHSCOPE_API_KEY。

延迟优化：
- 按句切分，第一句合成完立即开播，后续句子边播边预合成（与 edge-tts 一致）。

注意：dashscope 的 SpeechSynthesizerObjectPool 是进程级单例，但构造函数可重入——
每建一个引擎实例都会清空并重建连接池（预连默认音色 longxiaochun）、再起一条重连
线程，换音色/多实例时极易互相污染并卡死在「合成中」；且 call() 不传超时会无限
等待。因此弃用连接池，每次合成直连一个 SpeechSynthesizer（显式 20s 超时，用完即关）。

两套模型 × 两个档位（flash / plus），音色命名规则不同：
- cosyvoice-v3-flash / cosyvoice-v3-plus：音色是短名（如 longanyang、longanhuan_v3），
  两个档位通用同一套短名。
- qwen-audio-3.0-tts-flash / qwen-audio-3.0-tts-plus：音色必须带模型前缀
  （如 qwen-audio-3.0-tts-flash-longyingsongliu），且 flash/plus 前缀不同。

存储层统一存「短名」，运行时按 provider+tier 拼成完整 voice，避免前端手填前缀。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile

from backend.tts.base import TTSEngine

logger = logging.getLogger(__name__)

# 每段播报片段的最短字数（首句越小，开播越快）
MIN_CHUNK = 20

# provider → 模型名模板（{tier} 替换为 flash/plus）
_PROVIDER_MODELS = {
    "cosyvoice": "cosyvoice-v3-{tier}",
    "qwen": "qwen-audio-3.0-tts-{tier}",
}


def _resolve_voice(provider: str, tier: str, voice: str) -> str:
    """按 provider+tier 把短名音色拼成完整 voice 参数。"""
    if provider == "cosyvoice":
        # CosyVoice 音色即短名，档位通用
        return voice
    if provider == "qwen":
        # Qwen 音色需带模型前缀，前缀随档位变化
        return f"qwen-audio-3.0-tts-{tier}-{voice}"
    return voice


class CloudTTSEngine(TTSEngine):
    """阿里云付费云语音合成（CosyVoice v3 / Qwen-Audio-TTS）。"""

    def __init__(self, provider: str = "cosyvoice", tier: str = "flash", voice: str = "longanyang", api_key: str | None = None) -> None:
        if provider not in _PROVIDER_MODELS:
            raise ValueError(f"未知付费云 provider: {provider}")
        if tier not in ("flash", "plus"):
            raise ValueError(f"未知档位: {tier}")
        self._provider = provider
        self._tier = tier
        self._voice = voice
        self._api_key = api_key
        self._mixer_ready = False
        self._stop_requested = False

    @property
    def model(self) -> str:
        return _PROVIDER_MODELS[self._provider].format(tier=self._tier)

    def _ensure_mixer(self) -> None:
        if self._mixer_ready:
            return
        import pygame

        try:
            pygame.mixer.init(frequency=24000)
        except Exception:
            pass  # 无音频设备时容错
        self._mixer_ready = True

    def preflight(self) -> str | None:
        if not (self._api_key or os.environ.get("DASHSCOPE_API_KEY")):
            return "缺少阿里云百炼 API Key（DASHSCOPE_API_KEY）：请编辑该方案填入"
        return None

    def stop(self) -> None:
        """立刻停止当前播报（打断用）。"""
        self._stop_requested = True
        try:
            import pygame

            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass

    def close(self) -> None:
        """释放资源（程序退出/换方案时调用）。

        连接池已弃用，每次合成的连接随用随关，这里无池可释放。
        """

    async def speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._stop_requested = False
        chunks = self._split(text)
        self._ensure_mixer()
        try:
            # 第一句：合成完立即开播，同时预合成后续句子（降低开播延迟）
            first = await asyncio.to_thread(self._synthesize, chunks[0])
            if self._stop_requested:
                return
            rest_task = asyncio.create_task(asyncio.to_thread(self._synthesize_all, chunks[1:]))
            await asyncio.to_thread(self._play_blocking, first)
            rest = await rest_task
            for data in rest:
                if self._stop_requested:
                    break
                await asyncio.to_thread(self._play_blocking, data)
        except Exception as e:  # noqa: BLE001
            # 合成/播放失败不应中断主流程（文字回复仍显示在界面），但要记录供试听如实回报
            self._last_error = str(e)
            logger.warning("Cloud TTS speak failed: %s", e)

    async def synthesize(self, text: str) -> bytes:
        """只合成不播放（试听缓存用）：按句切分合成后拼接为整段 MP3 字节。"""
        text = (text or "").strip()
        if not text:
            return b""
        chunks = self._split(text)
        return await asyncio.to_thread(lambda: b"".join(self._synthesize_all(chunks)))

    def cache_fingerprint(self) -> str:
        return f"{self._provider}|{self._tier}|{self._voice}"

    def _synthesize(self, text: str) -> bytes:
        import dashscope
        from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

        voice = _resolve_voice(self._provider, self._tier, self._voice)
        key = self._api_key or os.environ.get("DASHSCOPE_API_KEY")
        if key:
            dashscope.api_key = key
        # 直连（弃用连接池）：每次合成独立建连、换音色互不污染，用完即关；
        # 显式超时防 SDK 内部无限等待（卡死在「合成中」的根源之一）
        synth = SpeechSynthesizer(
            model=self.model,
            voice=voice,
            format=AudioFormat.MP3_24000HZ_MONO_256KBPS,
        )
        try:
            data = synth.call(text, timeout_millis=20000)
        finally:
            try:
                synth.close()
            except Exception:
                pass
        if not data:
            raise RuntimeError(f"付费云 TTS 合成失败：{self.model} / {voice} 未返回音频")
        return data

    def _synthesize_all(self, chunks: list[str]) -> list[bytes]:
        return [self._synthesize(c) for c in chunks]

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
        """按标点/换行切分成播报片段，避免整段合成造成的开播延迟。

        句末标点（。！？；）与逗号都作为切分点；MIN_CHUNK 下限保证每段
        至少 20 字，避免切得太碎、语气不连贯。
        """
        parts = re.split(r"(?<=[。！？!?；;，,、\n])", text)
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
