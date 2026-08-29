"""LLM 工厂：按配置选择云端（DeepSeek/通义/OpenAI/GLM/Kimi）、本地（Ollama）或一体化（MiniCPM-o vLLM）。

云端各供应商均为 OpenAI 兼容协议，统一走 OpenAICompatClient，仅 base_url/模型/Key 不同。
支持多方案（llm.models[] + llm.active），未命中时回退旧单字段配置。
"""
from __future__ import annotations

from backend.config import (
    LLM_CLOUD_DEFAULTS,
    OMNI_BASE_URL,
    OMNI_MODEL,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    config,
    env,
)
from backend.llm.base import LLMClient
from backend.llm.openai_compat import OpenAICompatClient


def _build_scheme(m: dict) -> LLMClient:
    """按 models[] 里的一条方案构建客户端。

    方案字段名与前端保存一致（驼峰）：provider 为方案 id（deepseek/dashscope/
    openai/glm/kimi/ollama/omni），其余为 baseUrl / apiKey / model / temperature。
    """
    p = m.get("provider", "deepseek")
    model = m.get("model")
    base_url = m.get("baseUrl")
    api_key = m.get("apiKey")
    temperature = float(m.get("temperature", 0.3))

    if p == "ollama":
        return OpenAICompatClient(
            base_url=base_url or OLLAMA_BASE_URL,
            model=model or OLLAMA_MODEL,
            api_key="EMPTY",  # Ollama 无需真实 key
            temperature=temperature,
        )

    if p == "omni":
        return OpenAICompatClient(
            base_url=base_url or OMNI_BASE_URL,
            model=model or OMNI_MODEL,
            api_key=api_key or None,
            temperature=temperature,
        )

    # 云端 5 家（OpenAI 兼容）
    if p not in LLM_CLOUD_DEFAULTS:
        raise ValueError(f"未支持的 LLM provider: {p}")
    base_url_default, model_default, env_key = LLM_CLOUD_DEFAULTS[p]
    return OpenAICompatClient(
        base_url=base_url or base_url_default,
        model=model or model_default,
        api_key=api_key or (env(env_key) if env_key else None),
        temperature=temperature,
    )


def build_llm() -> LLMClient:
    # 多方案：读 active 指向的方案（与 asr/tts 一致）
    models = config.get("llm.models", None)
    if models:
        active = config.get("llm.active")
        m = next((x for x in models if x.get("id") == active), (models[0] if models else None))
        if m:
            return _build_scheme(m)

    # 回退旧单一字段（兼容旧配置）
    provider = config.get("llm.provider", "cloud")

    if provider == "local":
        cfg = config.section("llm.local")
        return OpenAICompatClient(
            base_url=cfg.get("base_url", OLLAMA_BASE_URL),
            model=cfg.get("model", OLLAMA_MODEL),
            api_key="EMPTY",
            temperature=float(cfg.get("temperature", 0.3)),
        )

    cfg = config.section("llm.cloud")
    provider_cloud = cfg.get("provider", "deepseek")
    temperature = float(cfg.get("temperature", 0.3))
    model = cfg.get("model")
    api_key = cfg.get("api_key") or None  # 优先读配置，回退环境变量

    if provider_cloud == "omni":
        omni = config.section("llm.omni")
        return OpenAICompatClient(
            base_url=omni.get("base_url", OMNI_BASE_URL),
            model=omni.get("model", OMNI_MODEL),
            api_key=omni.get("api_key") or None,
            temperature=temperature,
        )

    if provider_cloud not in LLM_CLOUD_DEFAULTS:
        raise ValueError(f"未支持的 LLM provider: {provider_cloud}")
    base_url_default, model_default, env_key = LLM_CLOUD_DEFAULTS[provider_cloud]
    return OpenAICompatClient(
        base_url=cfg.get("base_url", base_url_default),
        model=model or model_default,
        api_key=api_key or (env(env_key) if env_key else None),
        temperature=temperature,
    )
