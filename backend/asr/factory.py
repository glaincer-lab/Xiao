"""ASR 工厂：按配置选择云端 / 本地引擎。支持多方案（asr.models[] + asr.active）。"""
from __future__ import annotations

from backend.asr.base import ASREngine, ResultCallback
from backend.config import config, env


def _build_cloud(on_result: ResultCallback, model: str, api_key: str | None) -> ASREngine:
    import dashscope

    dashscope.api_key = api_key or env("DASHSCOPE_API_KEY")
    from backend.asr.cloud_paraformer import ParaformerRealtime

    sample_rate = 8000 if "8k" in str(model) else 16000
    return ParaformerRealtime(on_result, model=model, sample_rate=sample_rate)


def _build_local(on_result: ResultCallback, model: str, model_dir: str = "") -> ASREngine:
    from backend.asr.local_funasr import FunASRLocal

    return FunASRLocal(on_result, model=model, model_dir=model_dir)


def build_asr(on_result: ResultCallback) -> ASREngine:
    # 多方案：读 active 指向的方案
    models = config.get("asr.models", None)
    if models:
        active = config.get("asr.active")
        m = next((x for x in models if x.get("id") == active), (models[0] if models else None))
        if m:
            provider = m.get("provider", "cloud")
            if provider == "cloud":
                return _build_cloud(on_result, m.get("model", "paraformer-realtime-v2"), m.get("apiKey"))
            if provider == "local":
                return _build_local(on_result, m.get("model", "paraformer-zh"), m.get("localModelDir", ""))
            raise ValueError("一体化 ASR（omni）尚未接入")

    # 回退旧单一字段（兼容旧配置）
    provider = config.get("asr.provider", "cloud")
    if provider in ("cloud", "auto"):
        cloud_cfg = config.section("asr.cloud")
        p = cloud_cfg.get("provider", "aliyun")
        if p == "aliyun":
            try:
                model = cloud_cfg.get("model", "paraformer-realtime-v2")
                return _build_cloud(on_result, model, cloud_cfg.get("api_key"))
            except Exception:
                if provider == "cloud":
                    raise

    local_cfg = config.section("asr.local")
    return _build_local(on_result, local_cfg.get("model", "paraformer-zh"), local_cfg.get("model_dir", ""))
