"""TTS 工厂。支持多方案（tts.models[] + tts.active）。

provider 收敛为 6 类：
- edge       edge-tts 免费云
- qwen_rt    阿里云 Qwen 实时流式（qwen3-tts-flash-realtime，边合成边播，首音快）
- cosyvoice  阿里云 CosyVoice v3（flash / plus，非流式高音质）
- qwen       阿里云 Qwen-Audio-TTS（flash / plus，非流式）
- piper      本地 Piper（离线保底）
- omni       一体化 MiniCPM-o（本地 vLLM-omni）

付费云非流式（cosyvoice/qwen）通过 tier 字段区分 flash/plus，音色统一存短名，
运行时由 CloudTTSEngine 按 provider+tier 拼完整 voice。
"""
from __future__ import annotations

from backend.config import OMNI_BASE_URL, OMNI_MODEL, config
from backend.tts.base import TTSEngine
from backend.tts.edge_tts import EdgeTTS


def _build_edge(voice: str, rate: str) -> TTSEngine:
    return EdgeTTS(voice=voice, rate=rate)


def _build_cloud(
    provider: str, tier: str, voice: str, api_key: str | None, warm: bool = True
) -> TTSEngine:
    from backend.tts.cosyvoice import CloudTTSEngine

    # warm 仅为保持 _dispatch 签名兼容；连接池已弃用（见 cosyvoice.py），无需预热
    return CloudTTSEngine(provider=provider, tier=tier, voice=voice, api_key=api_key)


def _build_piper(model_path: str) -> TTSEngine:
    from backend.tts.piper import PiperEngine

    return PiperEngine(model_path=model_path)


def _build_qwen_rt(voice: str, api_key: str | None, warm: bool = True) -> TTSEngine:
    from backend.tts.qwen_realtime import QwenRealtimeTTS

    engine = QwenRealtimeTTS(voice=voice, api_key=api_key)
    if warm:
        engine.warm()  # 启动即预热连接，首句播报首音约 0.4s
    return engine


def _build_omni() -> TTSEngine:
    from backend.tts.omni import OmniTTSEngine

    omni_cfg = config.section("llm.omni")
    return OmniTTSEngine(
        base_url=omni_cfg.get("base_url", OMNI_BASE_URL),
        model=omni_cfg.get("model", OMNI_MODEL),
        api_key=omni_cfg.get("api_key"),
    )


# provider → 默认参数（音色存短名）
_PROVIDER_DEFAULTS = {
    "edge": {"voice": "zh-CN-YunjianNeural", "rate": "+30%"},
    "qwen_rt": {"voice": "Ethan"},
    "cosyvoice": {"tier": "flash", "voice": "longanyang"},
    "qwen": {"tier": "flash", "voice": "longyingsongliu"},
    "piper": {"model": "models/zh_CN-chaowen-medium.onnx"},
}


def _active_model() -> dict:
    """解析当前激活的 TTS 方案，统一成 model dict 形态（新多方案 / 旧单字段兼容）。"""
    models = config.get("tts.models", None)
    if models:
        active = config.get("tts.active")
        m = next((x for x in models if x.get("id") == active), (models[0] if models else None))
        if m:
            return m

    # 回退旧单一字段 → 映射成与新方案同一形态
    provider = str(config.get("tts.provider", "edge") or "edge")
    if provider == "cloud":  # 旧配置的 provider=cloud 视作 cosyvoice
        provider = "cosyvoice"
    m: dict = {"provider": provider}
    if provider in ("cosyvoice", "qwen"):
        cloud_cfg = config.section("tts.cloud")
        m["tier"] = config.get("tts.tier", "flash")
        m["voice"] = cloud_cfg.get("voice", _PROVIDER_DEFAULTS[provider]["voice"])
        m["apiKey"] = cloud_cfg.get("api_key")
    elif provider == "qwen_rt":
        m["voice"] = config.get("tts.voice", "Ethan")
        m["apiKey"] = config.get("tts.api_key")
    elif provider == "piper":
        m["piperModel"] = config.section("tts.piper").get("model", "models/zh_CN-chaowen-medium.onnx")
    else:  # edge
        m["voice"] = config.get("tts.voice", "zh-CN-YunjianNeural")
        m["rate"] = config.get("tts.rate", "+30%")
    return m


def _dispatch(m: dict, warm: bool = True) -> TTSEngine:
    provider = m.get("provider", "edge")
    if provider == "edge":
        return _build_edge(m.get("voice", "zh-CN-YunjianNeural"), m.get("rate", "+30%"))
    if provider == "qwen_rt":
        return _build_qwen_rt(m.get("voice", "Ethan"), m.get("apiKey"), warm=warm)
    if provider in ("cosyvoice", "qwen"):
        return _build_cloud(
            provider,
            m.get("tier", "flash"),
            m.get("voice", _PROVIDER_DEFAULTS[provider]["voice"]),
            m.get("apiKey"),
            warm=warm,
        )
    if provider == "piper":
        return _build_piper(m.get("piperModel", "models/zh_CN-chaowen-medium.onnx"))
    if provider == "omni":
        return _build_omni()
    raise ValueError(f"TTS 方案尚未接入: {provider}")


def build_tts() -> TTSEngine:
    """构建 TTS 真回退链：当前选定引擎 → 免费云兜底 edge-tts → 本地保底 Piper。

    任一云引擎超时/失败自动逐层降级，保证「能出声」；全链失败则记录并返回，
    主对话流水线不阻塞（speak 不堵死）。
    """
    active = _dispatch(_active_model())
    engines: list[TTSEngine] = [active]
    # 免费云兜底 edge-tts（当前若不是 edge）
    if not isinstance(active, EdgeTTS):
        try:
            engines.append(_build_edge("zh-CN-YunjianNeural", "+30%"))
        except Exception:  # noqa: BLE001
            pass
    # 本地保底 Piper（已配置且前置检查通过才纳入）
    try:
        piper = _build_piper(config.section("tts.piper").get("model", "models/zh_CN-chaowen-medium.onnx"))
        if piper.preflight() is None:
            engines.append(piper)
    except Exception:  # noqa: BLE001
        pass
    from backend.tts.chain import TTSChain
    # 即使单一引擎也包一层：用 wait_for 保证任何云引擎挂起都在超时内让出，杜绝 speak 永久阻塞
    return TTSChain(engines)


# 试听方案白名单：只接受这些键，前端多传的字段（id/name 等）一律忽略
_PREVIEW_KEYS = ("provider", "voice", "rate", "tier", "apiKey", "piperModel")


def build_preview_tts(
    model: dict | None = None, voice: str | None = None, rate: str | None = None
) -> TTSEngine:
    """试听用：优先按「指定方案」构建引擎——试听哪个方案就构建哪个；
    未指定时回退当前激活方案。voice/rate 覆盖仅在适用时生效（兼容旧调用）。

    - model：tts.models 里的一项（provider/voice/rate/tier/apiKey/piperModel）
    - edge：voice / rate 生效
    - qwen_rt / cosyvoice / qwen：voice 生效（无 rate 概念）
    - piper / omni：本地方案按方案参数构建，覆盖不适用
    """
    if isinstance(model, dict) and model.get("provider"):
        m: dict = {k: model[k] for k in _PREVIEW_KEYS if model.get(k) is not None}
    else:
        m = dict(_active_model())
    provider = m.get("provider", "edge")
    if voice and provider in ("edge", "qwen_rt", "cosyvoice", "qwen"):
        m["voice"] = voice
    if rate and provider == "edge":
        m["rate"] = rate
    return _dispatch(m, warm=False)  # 一次性试听实例：不预热，播完即释放
