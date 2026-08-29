"""服务商连通性测试（E2b）：按环节发最小请求，秒级返回 ok / 人话原因。

供 POST /api/provider/test 调用（设置页「测试连接」、首次启动向导共用）：
- LLM（DeepSeek/通义千问/OpenAI/Ollama/MiniCPM-o）：发一条 max_tokens=1 的最小对话；
- ASR 云方案：其 Key 走 DashScope，用兼容模式最便宜模型校验 Key 有效性；
- TTS：复用试听工厂构建一次性引擎，先 preflight 自检（缺 Key/缺声库给人话），
  再合成一个字验证真实连通；本地方案不联网只自检。
原则：最小开销、不产生业务调用；任何失败都转成一句人话，不向前端抛堆栈
（异常 → 人话的映射统一在 backend/errors.py，管线各处复用）。
"""
from __future__ import annotations

import asyncio
import time

from backend.config import (
    LLM_CLOUD_DEFAULTS,
    OMNI_BASE_URL,
    OMNI_MODEL,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    env,
)

PROBE_TIMEOUT = 10.0  # 单次连通探测超时（秒）：正常应答远快于此，超时多半是地址/网络填错

# DashScope 兼容模式探针模型：云 ASR/TTS 的 Key 都走 DashScope，用最便宜模型校验 Key 即可
DASHSCOPE_BASE_URL = LLM_CLOUD_DEFAULTS["dashscope"][0]
DASHSCOPE_PROBE_MODEL = "qwen-turbo"

_LLM_LABELS = {
    "deepseek": "DeepSeek",
    "dashscope": "通义千问",
    "openai": "OpenAI",
    "glm": "智谱 GLM",
    "kimi": "Kimi",
}

_TTS_LABELS = {
    "edge": "edge-tts",
    "qwen_rt": "Qwen 实时流式",
    "cosyvoice": "CosyVoice",
    "qwen": "Qwen-Audio-TTS",
    "piper": "Piper",
    "omni": "MiniCPM-o",
}


def _human_reason(e: Exception) -> str:
    """异常 → 一句人话；映射统一收在 backend/errors.py，管线各处复用（E2c）。"""
    from backend.errors import human_reason

    return human_reason(e, default=f"测试失败：{e}")


async def _probe_openai_compat(base_url: str, model: str, api_key: str | None) -> None:
    """对 OpenAI 兼容端点发一条最小对话；失败抛原始异常，由 _human_reason 转人话。"""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=base_url, api_key=api_key or "EMPTY", timeout=PROBE_TIMEOUT)
    try:
        await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            ),
            timeout=PROBE_TIMEOUT,
        )
    finally:
        await client.close()


async def _probe(base_url: str, model: str, api_key: str | None, ok_msg: str) -> dict:
    try:
        await _probe_openai_compat(base_url, model, api_key)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": _human_reason(e)}
    return {"ok": True, "msg": ok_msg}


async def _test_llm(m: dict) -> dict:
    p = str(m.get("provider") or "deepseek").lower()
    label = _LLM_LABELS.get(p)
    if p == "ollama":
        return await _probe(
            m.get("baseUrl") or OLLAMA_BASE_URL,
            m.get("model") or OLLAMA_MODEL,
            "EMPTY",
            "连通正常：已连上本地 Ollama",
        )
    if p == "omni":
        return await _probe(
            m.get("baseUrl") or OMNI_BASE_URL,
            m.get("model") or OMNI_MODEL,
            m.get("apiKey") or None,
            "连通正常：已连上本地 vLLM-omni 服务",
        )
    if p not in LLM_CLOUD_DEFAULTS:
        return {
            "ok": False,
            "msg": f"未支持的 LLM 服务商: {p}（可选：DeepSeek / 通义千问 / OpenAI / Ollama / MiniCPM-o）",
        }
    base_url_d, model_d, env_key = LLM_CLOUD_DEFAULTS[p]
    api_key = m.get("apiKey") or (env(env_key) if env_key else None)
    if not api_key:
        return {
            "ok": False,
            "msg": f"尚未填写 {label or p} 的 API Key：请到服务商控制台创建后粘贴到设置里",
        }
    return await _probe(
        m.get("baseUrl") or base_url_d,
        m.get("model") or model_d,
        api_key,
        f"连通正常：{label or p} 应答成功",
    )


async def _test_asr(m: dict) -> dict:
    p = str(m.get("provider") or "cloud").lower()
    if p == "local":
        return {"ok": True, "msg": "本地识别引擎无需密钥；实际效果请用「麦克风测试」验证"}
    if p == "omni":
        return await _probe(
            OMNI_BASE_URL,
            OMNI_MODEL,
            None,
            "连通正常：本地 vLLM-omni 服务可达",
        )
    # cloud：识别模型没有"免费试一发"的最小请求，用同一 DashScope Key 的聊天探针校验有效性
    api_key = m.get("apiKey") or env("DASHSCOPE_API_KEY")
    if not api_key:
        return {"ok": False, "msg": "尚未填写 API Key：请粘贴 DashScope（通义千问）Key，识别服务才能连通"}
    return await _probe(
        DASHSCOPE_BASE_URL,
        DASHSCOPE_PROBE_MODEL,
        api_key,
        "Key 有效：DashScope 校验通过，识别服务可正常连通",
    )


async def _test_tts(m: dict) -> dict:
    from backend.tts.factory import build_preview_tts

    try:
        engine = build_preview_tts(model=m)
    except ValueError as e:
        return {"ok": False, "msg": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": _human_reason(e)}
    try:
        problem = engine.preflight()
        if problem:
            return {"ok": False, "msg": problem}
        data = await asyncio.wait_for(engine.synthesize("好"), timeout=PROBE_TIMEOUT)
        if not data:
            return {"ok": False, "msg": "连通异常：引擎未返回音频，请检查该方案配置"}
        provider = str(m.get("provider") or "")
        return {"ok": True, "msg": f"连通正常：{_TTS_LABELS.get(provider, 'TTS')} 合成测试通过"}
    except NotImplementedError:
        return {"ok": False, "msg": "该引擎暂不支持合成测试，请以实际播报为准"}
    except ImportError as e:
        return {"ok": False, "msg": f"本地引擎依赖未安装：{e}（本地引擎按需安装，见 requirements-local-tts.txt）"}
    except asyncio.TimeoutError:
        return {"ok": False, "msg": "连接超时：请检查网络与该方案的 API Key 后重试"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": _human_reason(e)}
    finally:
        # 一次性实例用完即释放（close/stop），避免云引擎连接池被测试反复占用
        release = getattr(engine, "close", None) or getattr(engine, "stop", None)
        if callable(release):
            try:
                release()
            except Exception:  # noqa: BLE001
                pass


async def test_provider(target: str, model: dict) -> dict:
    """对外入口：target=llm/asr/tts；model 为对应环节 models[] 里的一条方案（驼峰字段）。"""
    target = str(target or "").strip().lower()
    m = model if isinstance(model, dict) else {}
    t0 = time.perf_counter()
    if target == "llm":
        r = await _test_llm(m)
    elif target == "asr":
        r = await _test_asr(m)
    elif target == "tts":
        r = await _test_tts(m)
    else:
        r = {"ok": False, "msg": "未知测试对象：target 须为 llm / asr / tts 之一"}
    r.setdefault("latency_ms", int((time.perf_counter() - t0) * 1000))
    return r


# ---- E4 健康状态灯：按当前激活方案逐项探测，供 GET /api/health/probe 复用 ----

_HEALTH_LABELS = {
    "asr": "语音识别（ASR）",
    "llm": "大脑（LLM）",
    "tts": "语音合成（TTS）",
}


def resolve_active(cfg: dict, key: str) -> tuple[dict, str]:
    """从 config 的 {key}.models[] 里挑出 active 方案；找不到就返回空 dict（按默认配置探测）。"""
    block = cfg.get(key) if isinstance(cfg.get(key), dict) else {}
    active_id = block.get("active")
    for m in block.get("models") or []:
        if isinstance(m, dict) and m.get("id") == active_id:
            return m, str(m.get("name") or active_id or "")
    return {}, ""


async def probe_component(key: str, model: dict, scheme: str = "") -> dict:
    """探测单环节并补齐状态灯字段（label/scheme）；msg 沿用 test_provider 的人话。"""
    r = await test_provider(key, model)
    return {
        "key": key,
        "label": _HEALTH_LABELS.get(key, key),
        "scheme": scheme or "默认方案",
        "ok": bool(r.get("ok")),
        "msg": str(r.get("msg") or ""),
        "latency_ms": r.get("latency_ms"),
    }


def agent_item(available: bool) -> dict:
    """agent（DSH）环节的状态灯项：只查本机能否找到 dsh 命令，不实际拉起（秒回）。"""
    return {
        "key": "agent",
        "label": "执行 agent（DSH）",
        "scheme": "本机 dsh 命令",
        "ok": bool(available),
        "msg": (
            "本机已找到 dsh 命令，可以正常执行"
            if available
            else "本机没找到 dsh 命令：请先安装 DSH 并确认在 PATH 里，再重启小二（见 README「安装」）"
        ),
        "latency_ms": 0,
    }
