"""TTS 工厂。支持多方案（tts.models[] + tts.active）。

provider 收敛为 5 类：
- edge       edge-tts 免费云
- cosyvoice  阿里云 CosyVoice v3（flash / plus）
- qwen       阿里云 Qwen-Audio-TTS（flash / plus）
- piper      本地 Piper（离线保底）
- omni       一体化 MiniCPM-o（本地 vLLM-omni）

付费云（cosyvoice/qwen）通过 tier 字段区分 flash/plus，音色统一存短名，
运行时由 CloudTTSEngine 按 provider+tier 拼完整 voice。
"""
from __future__ import annotations

from backend.config import config
from backend.tts.base import TTSEngine
from backend.tts.edge_tts import EdgeTTS


def _build_edge(voice: str, rate: str) -> TTSEngine:
    return EdgeTTS(voice=voice, rate=rate)


def _build_cloud(provider: str, tier: str, voice: str, api_key: str | None) -> TTSEngine:
    from backend.tts.cosyvoice import CloudTTSEngine

    return CloudTTSEngine(provider=provider, tier=tier, voice=voice, api_key=api_key)


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


# provider → 默认参数（音色存短名）
_PROVIDER_DEFAULTS = {
    "edge": {"voice": "zh-CN-YunjianNeural", "rate": "+30%"},
    "cosyvoice": {"tier": "flash", "voice": "longanyang"},
    "qwen": {"tier": "flash", "voice": "longyingsongliu"},
    "piper": {"model": "models/zh_CN-huayan-medium.onnx"},
}


def build_tts() -> TTSEngine:
    models = config.get("tts.models", None)
    if models:
        active = config.get("tts.active")
        m = next((x for x in models if x.get("id") == active), (models[0] if models else None))
        if m:
            provider = m.get("provider", "edge")
            if provider == "edge":
                return _build_edge(m.get("voice", "zh-CN-YunjianNeural"), m.get("rate", "+30%"))
            if provider in ("cosyvoice", "qwen"):
                return _build_cloud(
                    provider,
                    m.get("tier", "flash"),
                    m.get("voice", _PROVIDER_DEFAULTS[provider]["voice"]),
                    m.get("apiKey"),
                )
            if provider == "piper":
                return _build_piper(m.get("piperModel", "models/zh_CN-huayan-medium.onnx"))
            if provider == "omni":
                return _build_omni()
            raise ValueError(f"TTS 方案尚未接入: {provider}")

    # 回退旧单一字段（兼容旧配置）
    provider = config.get("tts.provider", "edge")
    if provider == "edge":
        return _build_edge(
            config.get("tts.voice", "zh-CN-YunjianNeural"),
            config.get("tts.rate", "+30%"),
        )
    if provider in ("cosyvoice", "qwen", "cloud"):
        # 旧配置的 provider=cloud 视作 cosyvoice
        p = "cosyvoice" if provider == "cloud" else provider
        cloud_cfg = config.section("tts.cloud")
        return _build_cloud(
            p,
            config.get("tts.tier", "flash"),
            cloud_cfg.get("voice", _PROVIDER_DEFAULTS[p]["voice"]),
            cloud_cfg.get("api_key"),
        )
    if provider == "piper":
        piper_cfg = config.section("tts.piper")
        return _build_piper(piper_cfg.get("model", "models/zh_CN-huayan-medium.onnx"))
    if provider == "omni":
        return _build_omni()
    raise ValueError(f"未支持的 TTS provider: {provider}")
