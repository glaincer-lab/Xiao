"""ASR 工厂：按配置选择云端 / 本地引擎。支持多方案（asr.models[] + asr.active）。"""
from __future__ import annotations

from backend.asr.base import ASREngine, ResultCallback
from backend.config import config, env


def _build_cloud(on_result: ResultCallback, model: str, api_key: str | None) -> ASREngine:
    import dashscope

    dashscope.api_key = api_key or env("DASHSCOPE_API_KEY")

    # qwen3-asr-flash-realtime 走 OmniRealtime（WebSocket 全双工）接口，与 Recognition 不同
    if model == "qwen3-asr-flash-realtime":
        from backend.asr.qwen3_realtime import Qwen3ASRRealtime

        return Qwen3ASRRealtime(on_result, api_key=api_key, model=model)

    from backend.asr.cloud_paraformer import ParaformerRealtime

    sample_rate = 8000 if "8k" in str(model) else 16000
    return ParaformerRealtime(on_result, model=model, sample_rate=sample_rate)


def _build_local(on_result: ResultCallback, model: str, model_dir: str = "") -> ASREngine:
    from backend.asr.local_funasr import FunASRLocal

    return FunASRLocal(on_result, model=model, model_dir=model_dir)


def _build_omni(on_result: ResultCallback) -> ASREngine:
    from backend.asr.omni import OmniASREngine

    omni_cfg = config.section("llm.omni")
    return OmniASREngine(
        on_result,
        base_url=omni_cfg.get("base_url", "http://localhost:8000/v1"),
        model=omni_cfg.get("model", "openbmb/MiniCPM-o-4_5"),
        api_key=omni_cfg.get("api_key"),
    )


def build_asr(on_result: ResultCallback) -> ASREngine:
    # 多方案：读 active 指向的方案
    models = config.get("asr.models", None)
    if models:
        active = config.get("asr.active")
        m = next((x for x in models if x.get("id") == active), (models[0] if models else None))
        if m:
            provider = m.get("provider", "cloud")
            if provider == "cloud":
                return _build_cloud(on_result, m.get("model", "fun-asr-flash-8k-realtime"), m.get("apiKey"))
            if provider == "local":
                return _build_local(on_result, m.get("model", "paraformer-zh"), m.get("localModelDir", ""))
            if provider == "omni":
                return _build_omni(on_result)
            raise ValueError(f"未支持的 ASR provider: {provider}")

    # 回退旧单一字段（兼容旧配置）
    provider = config.get("asr.provider", "cloud")
    if provider == "omni":
        return _build_omni(on_result)
    if provider in ("cloud", "auto"):
        cloud_cfg = config.section("asr.cloud")
        p = cloud_cfg.get("provider", "aliyun")
        if p == "aliyun":
            try:
                model = cloud_cfg.get("model", "fun-asr-flash-8k-realtime")
                return _build_cloud(on_result, model, cloud_cfg.get("api_key"))
            except Exception:
                if provider == "cloud":
                    raise

    local_cfg = config.section("asr.local")
    return _build_local(on_result, local_cfg.get("model", "paraformer-zh"), local_cfg.get("model_dir", ""))
