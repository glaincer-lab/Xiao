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

# 允许透传 top_k（经 extra_body）的供应商：其余家（DeepSeek/OpenAI/Kimi）仅保存不发送，避免 400
_TOPK_PASSTHROUGH = {"dashscope", "glm", "ollama", "omni"}


def _sampling(
    provider: str,
    top_p: float | str | None = None,
    top_k: int | str | None = None,
    max_tokens: int | str | None = None,
) -> dict:
    """把采样类参数规整成 OpenAICompatClient 可接收的形式；留空一律不发送。"""
    out: dict = {}

    def _num(v):  # noqa: ANN001
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    tp = _num(top_p)
    if tp is not None:
        out["top_p"] = min(max(tp, 0.0), 1.0)
    mt = _num(max_tokens)
    if mt is not None and mt > 0:
        out["max_tokens"] = int(mt)
    tk = _num(top_k)
    if tk is not None and tk > 0 and provider in _TOPK_PASSTHROUGH:
        out["extra_body"] = {"top_k": int(tk)}
    return out


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
    sampling = _sampling(p, m.get("topP"), m.get("topK"), m.get("contextOutput"))

    if p == "ollama":
        return OpenAICompatClient(
            base_url=base_url or OLLAMA_BASE_URL,
            model=model or OLLAMA_MODEL,
            api_key="EMPTY",  # Ollama 无需真实 key
            temperature=temperature,
            **sampling,
        )

    if p == "omni":
        return OpenAICompatClient(
            base_url=base_url or OMNI_BASE_URL,
            model=model or OMNI_MODEL,
            api_key=api_key or None,
            temperature=temperature,
            **sampling,
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
        **sampling,
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
            **_sampling(
                "ollama",
                cfg.get("top_p"),
                cfg.get("top_k"),
                cfg.get("max_tokens"),
            ),
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
        **_sampling(
            provider_cloud,
            cfg.get("top_p"),
            cfg.get("top_k"),
            cfg.get("max_tokens"),
        ),
    )


def build_llm_by_id(scheme_id: str) -> LLMClient:
    """按 ``llm.models[]`` 中某方案的 ``id`` 构建客户端（复用多方案可切能力）。

    供 backend/orchestrator/ 按「规划(planner)/执行(worker)」角色定向选模型（贵/廉价）。
    未命中时抛 ``ValueError``（调用方自行回退），不改动现有 ``build_llm`` 行为。
    """
    models = config.get("llm.models", None)
    if models:
        m = next((x for x in models if x.get("id") == scheme_id), None)
        if m:
            return _build_scheme(m)
    raise ValueError(f"llm.models 中未找到方案 id={scheme_id!r}")
