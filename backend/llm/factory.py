"""LLM 工厂：按配置选择云端（DeepSeek/通义/OpenAI/GLM/Kimi）、本地（Ollama）或一体化（MiniCPM-o vLLM）。

云端各供应商均为 OpenAI 兼容协议，统一走 OpenAICompatClient，仅 base_url/模型/Key 不同。
支持多方案（llm.models[] + llm.active），未命中时回退旧单字段配置。
"""
from __future__ import annotations

from backend.config import config, env
from backend.llm.base import LLMClient
from backend.llm.openai_compat import OpenAICompatClient

# 云端供应商：默认 base_url、默认模型、回退用的环境变量 key（None 表示仅用配置里的 Key）
_CLOUD_DEFAULTS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-v4-pro", "DEEPSEEK_API_KEY"),
    "dashscope": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus", "DASHSCOPE_API_KEY"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY"),
    "glm": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash", None),
    "kimi": ("https://api.moonshot.cn/v1", "moonshot-v1-8k", None),
}


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
            base_url=base_url or "http://localhost:11434/v1",
            model=model or "qwen2.5:7b",
            api_key="EMPTY",  # Ollama 无需真实 key
            temperature=temperature,
        )

    # 云端 5 家（OpenAI 兼容）
    if p not in _CLOUD_DEFAULTS:
        raise ValueError(f"未支持的 LLM provider: {p}")
    base_url_default, model_default, env_key = _CLOUD_DEFAULTS[p]
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
            base_url=cfg.get("base_url", "http://localhost:11434/v1"),
            model=cfg.get("model", "qwen2.5:7b"),
            api_key="EMPTY",
            temperature=float(cfg.get("temperature", 0.3)),
        )

    cfg = config.section("llm.cloud")
    p = cfg.get("provider", "deepseek")
    temperature = float(cfg.get("temperature", 0.3))
    model = cfg.get("model")
    api_key = cfg.get("api_key") or None  # 优先读配置，回退环境变量

    if p not in _CLOUD_DEFAULTS:
        raise ValueError(f"未支持的 LLM provider: {p}")
    base_url_default, model_default, env_key = _CLOUD_DEFAULTS[p]
    return OpenAICompatClient(
        base_url=cfg.get("base_url", base_url_default),
        model=model or model_default,
        api_key=api_key or (env(env_key) if env_key else None),
        temperature=temperature,
    )
