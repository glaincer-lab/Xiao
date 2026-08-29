"""端侧离线链路自检（v3）：四个环节是否全为本地引擎，断网能不能跑通全链。

验收口径：断网仍能 唤醒 → 转写 → 本地回答 → 播报。
只读配置、零网络、秒回；本地服务（Ollama / vLLM-omni）是否已启动、
云 Key 是否有效属于运行时连通性，交给健康状态灯逐项探测（/api/health/probe）。
本检查作为灯组里的一盏（offline）随探测一起返回，无需单独接口。
"""
from __future__ import annotations

from backend.provider_test import resolve_active

# 各环节的本地引擎取值（方案内字段：wake 看 engine，其余看 provider）
_LOCAL_ENGINES = {
    "wake_word": {"sherpa", "omni"},
    "asr": {"local", "omni"},
    "llm": {"ollama", "omni", "local"},
    "tts": {"piper", "omni"},
}

# 无 models[] / active 未命中时，回退旧单字段配置的读取路径
_LEGACY_PATHS = {
    "wake_word": ("wake_word.engine",),
    "asr": ("asr.provider",),
    "llm": ("llm.provider", "llm.cloud.provider"),
    "tts": ("tts.provider",),
}

_LABELS = {
    "wake_word": "唤醒",
    "asr": "识别",
    "llm": "大脑（LLM）",
    "tts": "播报",
}

# 引擎 → 一句人话（omni 一体化也全在本机，但需先启动本地 vLLM-omni 服务）
_ENGINE_MSG = {
    ("wake_word", "sherpa"): "本地 sherpa-ONNX 关键词唤醒，断网可用",
    ("wake_word", "omni"): "MiniCPM-o 唤醒在本机（需先启动 vLLM-omni 服务）",
    ("asr", "local"): "本地 FunASR 转写，断网可用",
    ("asr", "omni"): "MiniCPM-o 转写在本机（需先启动 vLLM-omni 服务）",
    ("llm", "ollama"): "本地 Ollama 回答，断网可用",
    ("llm", "local"): "本地 Ollama 回答，断网可用",
    ("llm", "omni"): "MiniCPM-o 回答在本机（需先启动 vLLM-omni 服务）",
    ("tts", "piper"): "本地 Piper 播报，断网可用",
    ("tts", "omni"): "MiniCPM-o 播报在本机（需先启动 vLLM-omni 服务）",
}


def _legacy(cfg: dict, *paths: str) -> str:
    for path in paths:
        node = cfg
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if node:
            return str(node).strip().lower()
    return ""


def _active_engine(cfg: dict, key: str) -> str:
    m, _ = resolve_active(cfg, key)
    field = "engine" if key == "wake_word" else "provider"
    if m.get(field):
        return str(m[field]).strip().lower()
    return _legacy(cfg, *_LEGACY_PATHS.get(key, ()))


def check_offline(cfg: dict) -> dict:
    """判四环节当前激活方案是否全为本地引擎；返回 ready / items / msg（人话）。"""
    items = []
    for key in ("wake_word", "asr", "llm", "tts"):
        engine = _active_engine(cfg, key)
        local = engine in _LOCAL_ENGINES.get(key, set())
        items.append({
            "key": key,
            "label": _LABELS[key],
            "local": local,
            "engine": engine,
            "msg": _ENGINE_MSG.get((key, engine), f"云端/外部引擎（{engine or '未配置'}），断网不可用"),
        })
    ready = all(i["local"] for i in items)
    if ready:
        msg = "四个环节全是本地引擎：断网也能 唤醒 → 识别 → 本地回答 → 播报（MiniCPM-o 需先启动本地服务）"
    else:
        bad = "、".join(i["label"] for i in items if not i["local"])
        msg = f"断网还跑不通：「{bad}」还不是本地方案——到设置里切到本地方案即可（见 README「离线模式」）"
    return {"ready": ready, "items": items, "msg": msg}


def offline_item(cfg: dict) -> dict:
    """转成健康灯格式（E4）：作为灯组里的一盏随 /api/health/probe 一起返回。"""
    r = check_offline(cfg)
    return {
        "key": "offline",
        "label": "离线就绪（断网可用）",
        "scheme": "四环节本地链",
        "ok": r["ready"],
        "msg": r["msg"],
        "latency_ms": 0,
    }
