"""TTS：阿里云付费云语音合成（CosyVoice v3 / Qwen-Audio-TTS）+ pygame 播放。

走 dashscope 的 tts_v2 非实时接口（dashscope.audio.tts_v2.SpeechSynthesizer），
合成 MP3 后用 pygame 播放。API Key 复用阿里云百炼的 DASHSCOPE_API_KEY。

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
import tempfile

from backend.tts.base import TTSEngine

logger = logging.getLogger(__name__)

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
            logger.warning("Cloud TTS speak failed: %s", e)

    def _synthesize(self, text: str) -> bytes:
        import dashscope
        from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

        key = self._api_key or os.environ.get("DASHSCOPE_API_KEY")
        if key:
            dashscope.api_key = key  # 留空则用环境变量 DASHSCOPE_API_KEY

        voice = _resolve_voice(self._provider, self._tier, self._voice)
        synthesizer = SpeechSynthesizer(
            model=self.model,
            voice=voice,
            format=AudioFormat.MP3_24000HZ_MONO_256KBPS,
        )
        data = synthesizer.call(text)
        if not data:
            raise RuntimeError(f"付费云 TTS 合成失败：{self.model} / {voice} 未返回音频")
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
