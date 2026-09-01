"""TTS 工厂。支持多方案（tts.models[] + tts.active）。

provider 收敛为 4 类：
- edge       edge-tts 免费云
- qwen_rt    阿里云 Qwen 实时流式（qwen3-tts-flash-realtime，边合成边播，首音快）
- piper      本地 Piper（离线保底）
- omni       一体化 MiniCPM-o（本地 vLLM-omni）
"""
from __future__ import annotations

import os

from backend.config import OMNI_BASE_URL, OMNI_MODEL, active_model, config
from backend.tts.base import TTSEngine
from backend.tts.edge_tts import EdgeTTS


def _build_edge(voice: str, rate: str) -> TTSEngine:
    return EdgeTTS(voice=voice, rate=rate)


def _build_piper(model_path: str) -> TTSEngine:
    from backend.tts.piper import PiperEngine

    return PiperEngine(model_path=model_path)


def _build_qwen_rt(voice: str, api_key: str | None, warm: bool = True) -> TTSEngine:
    from backend.tts.qwen_realtime import QwenRealtimeTTS

    engine = QwenRealtimeTTS(voice=voice, api_key=api_key)
    # 无 Key 时预热必失败（且产生告警日志），跳过 warm，播报时再走 Piper/edge 降级
    if warm and (api_key or os.environ.get("DASHSCOPE_API_KEY")):
        engine.warm()  # 有 Key 才预热连接，首句播报首音约 0.4s
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
    "piper": {"model": "models/zh_CN-chaowen-medium.onnx"},
}


def _active_model() -> dict:
    """解析当前激活的 TTS 方案，统一成 model dict 形态（新多方案 / 旧单字段兼容）。"""
    m = active_model(config, "tts")
    if m:
        return m

    # 回退旧单一字段 → 映射成与新方案同一形态
    provider = str(config.get("tts.provider", "edge") or "edge")
    m: dict = {"provider": provider}
    if provider == "qwen_rt":
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
    from backend.authorization import AuthorizationCenter

    m = _active_model()
    provider = m.get("provider", "edge")
    # 未授权上云（edge / qwen_rt 为云）→ 当前引擎改为本地 Piper（不抛异常）
    if provider in ("edge", "qwen_rt") and not AuthorizationCenter().is_granted("cloud_tts"):
        m = {"provider": "piper", "piperModel": config.section("tts.piper").get("model", _PROVIDER_DEFAULTS["piper"]["model"])}
    active = _dispatch(m)
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
_PREVIEW_KEYS = ("provider", "voice", "rate", "apiKey", "piperModel")


def build_preview_tts(
    model: dict | None = None, voice: str | None = None, rate: str | None = None
) -> TTSEngine:
    """试听用：优先按「指定方案」构建引擎——试听哪个方案就构建哪个；
    未指定时回退当前激活方案。voice/rate 覆盖仅在适用时生效（兼容旧调用）。

    - model：tts.models 里的一项（provider/voice/rate/apiKey/piperModel）
    - edge：voice / rate 生效
    - qwen_rt：voice 生效（无 rate 概念）
    - piper / omni：本地方案按方案参数构建，覆盖不适用
    """
    if isinstance(model, dict) and model.get("provider"):
        m: dict = {k: model[k] for k in _PREVIEW_KEYS if model.get(k) is not None}
    else:
        m = dict(_active_model())
    provider = m.get("provider", "edge")
    if voice and provider in ("edge", "qwen_rt"):
        m["voice"] = voice
    if rate and provider == "edge":
        m["rate"] = rate
    return _dispatch(m, warm=False)  # 一次性试听实例：不预热，播完即释放
