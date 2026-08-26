"""TTS 工厂。支持多方案（tts.models[] + tts.active）。"""
from __future__ import annotations

from backend.config import config
from backend.tts.base import TTSEngine
from backend.tts.edge_tts import EdgeTTS


def _build_edge(voice: str, rate: str) -> TTSEngine:
    return EdgeTTS(voice=voice, rate=rate)


def _build_cloud(voice: str, api_key: str | None) -> TTSEngine:
    from backend.tts.cosyvoice import CosyVoiceEngine

    return CosyVoiceEngine(voice=voice, api_key=api_key)


def build_tts() -> TTSEngine:
    models = config.get("tts.models", None)
    if models:
        active = config.get("tts.active")
        m = next((x for x in models if x.get("id") == active), (models[0] if models else None))
        if m:
            provider = m.get("provider", "edge")
            if provider == "edge":
                return _build_edge(m.get("voice", "zh-CN-XiaoxiaoNeural"), m.get("rate", "+0%"))
            if provider == "cloud":
                return _build_cloud(m.get("voice", "longxiaochun"), m.get("apiKey"))
            raise ValueError(f"TTS 方案尚未接入: {provider}")

    provider = config.get("tts.provider", "edge")
    if provider == "edge":
        return _build_edge(
            config.get("tts.voice", "zh-CN-XiaoxiaoNeural"),
            config.get("tts.rate", "+0%"),
        )
    if provider == "cloud":
        cloud_cfg = config.section("tts.cloud")
        return _build_cloud(cloud_cfg.get("voice", "longxiaochun"), cloud_cfg.get("api_key"))
    raise ValueError(f"未支持的 TTS provider: {provider}")
