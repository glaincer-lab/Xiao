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


def _build_piper(model_path: str) -> TTSEngine:
    from backend.tts.piper import PiperEngine

    return PiperEngine(model_path=model_path)


def _build_omni() -> TTSEngine:
    from backend.tts.omni import OmniTTSEngine

    omni_cfg = config.section("llm.omni")
    return OmniTTSEngine(
        base_url=omni_cfg.get("base_url", "http://localhost:8000/v1"),
        model=omni_cfg.get("model", "openbmb/MiniCPM-o-4_5"),
        api_key=omni_cfg.get("api_key"),
    )


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
            if provider == "piper":
                return _build_piper(m.get("piperModel", ""))
            if provider == "omni":
                return _build_omni()
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
    if provider == "piper":
        piper_cfg = config.section("tts.piper")
        return _build_piper(piper_cfg.get("model", ""))
    if provider == "omni":
        return _build_omni()
    raise ValueError(f"未支持的 TTS provider: {provider}")
