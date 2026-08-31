"""M1 存储治理：向量层失效标记 + 容量上限清理 + P0 保护 + 存储满阈值（后台触发式）。

对应设计书 M1-vector-memory.md §4.3 / §5：
- 时间窗口失效：向量条目超过长期窗口（默认 3650 天）→ 失效标记 + 摘要指针（非物理删除）。
- 容量上限清理：存储超预算（默认 300MB，流水+向量合计）→ 失效最旧的「天单元」，到回预算，
  **最低保留最近 1 个完整单元、单单元不切碎**；仅用户确认后调用，绝不静默。
- P0（共同记忆 + 成长三轨）：kind ∈ (person, milestone) 或显式 meta.p0，**永不失效**，
  不进任何自动清理；仅用户手动 forget 真删。
- 存储满提示：占用 80% → warn（提醒，可暂缓）/ 95% → critical（强提示），由 UI 弹窗，
  用户选「清理 / 提升空间 / 暂不处理」。

本模块只做「判断 + 失效动作」的纯逻辑；估算字节、分组、P0 判定均可单测。仅标准库。
"""
from __future__ import annotations

import datetime as _dt
import time
from pathlib import Path
from typing import Any

from backend.config import config

# P0 标签（设计书 §3.1/§4.3：人设/亲友 = person，成长里程碑 = milestone）
P0_KINDS = ("person", "milestone")

# 存储满提示阈值（设计书 §5）
THRESHOLD_WARN = 0.80
THRESHOLD_CRITICAL = 0.95

# 失效摘要指针的正文截断长度（字符）
POINTER_MAX_LEN = 60


def is_p0(meta: object) -> bool:
    """P0 判定：显式 meta.p0，或 kind ∈ (person, milestone)。"""
    m = meta if isinstance(meta, dict) else {}
    if m.get("p0"):
        return True
    return m.get("kind") in P0_KINDS


def summary_pointer(text: object, max_len: int = POINTER_MAX_LEN) -> str:
    """由失效条目的原文生成一句「摘要指针」（可回溯，非物理删除）。"""
    t = str(text or "").strip()
    if not t:
        return "（已随超长期淡忘，仅留印记）"
    head = t[: max(1, int(max_len))]
    if len(t) > int(max_len):
        head += "…"
    return f"{head}（细节已随超长期淡忘，仅留印记）"


def enforce_vector_retention(
    store: Any,
    *,
    long_window_days: int = 3650,
    now_ts: float | None = None,
) -> dict[str, int]:
    """时间窗口失效：ts 超过 long_window_days 天且非 P0 → invalidate（自动，后台）。

    返回 {"invalidated": 失效数, "skipped_p0": 跳过的 P0 数}。已失效条目不再重复处理。
    """
    now = float(now_ts if now_ts is not None else time.time())
    threshold_ts = now - float(long_window_days) * 86400.0
    invalidated = 0
    skipped_p0 = 0
    for r in store.all_records():
        meta = r.get("meta", {}) if isinstance(r.get("meta"), dict) else {}
        if meta.get("status") == "invalidated":
            continue
        if float(r.get("ts", 0)) >= threshold_ts:
            continue  # 未超窗口
        if is_p0(meta):
            skipped_p0 += 1
            continue
        store.invalidate(str(r["id"]), summary_pointer(r.get("text", "")))
        invalidated += 1
    return {"invalidated": invalidated, "skipped_p0": skipped_p0}


def enforce_capacity(
    store: Any,
    *,
    budget_bytes: int,
    now_ts: float | None = None,
    dim: int = 512,
) -> dict[str, int]:
    """容量上限清理：超预算 → 失效最旧的「天单元」，到回预算，最低保留最近 1 单元。

    - 单元=天：按 ts 归天，旧天先失效，**单天整体失效不切碎**。
    - 最低保留最近 1 个完整单元（最新的一天不动）。
    - P0 永不失效（跳过，仅计数）。
    仅用户确认后调用（绝不静默）。返回 {"invalidated", "skipped_p0", "units_kept"}。
    """
    now = float(now_ts if now_ts is not None else time.time())
    _ = now
    active = [
        r for r in store.all_records()
        if (r.get("meta", {}) if isinstance(r.get("meta"), dict) else {}).get("status") != "invalidated"
    ]
    if not active:
        return {"invalidated": 0, "skipped_p0": 0, "units_kept": 0}

    days: dict[str, list[dict[str, Any]]] = {}
    for r in active:
        d = _day_of(r.get("ts", 0.0))
        days.setdefault(d, []).append(r)
    day_keys = sorted(days.keys())  # 旧 → 新

    def _active_bytes() -> int:
        return sum(
            _record_bytes(r, dim)
            for r in store.all_records()
            if (r.get("meta", {}) if isinstance(r.get("meta"), dict) else {}).get("status") != "invalidated"
        )

    used = _active_bytes()
    invalidated = 0
    skipped_p0 = 0
    # 从最旧的天开始失效，但最低保留最近 1 个单元（day_keys[:-1] 不动最后一天）
    for d in day_keys[:-1]:
        if used <= budget_bytes:
            break
        for r in days[d]:
            meta = r.get("meta", {}) if isinstance(r.get("meta"), dict) else {}
            if is_p0(meta):
                skipped_p0 += 1
                continue
            store.invalidate(str(r["id"]), summary_pointer(r.get("text", "")))
            invalidated += 1
        used = _active_bytes()
    return {"invalidated": invalidated, "skipped_p0": skipped_p0, "units_kept": 1}


def storage_usage_bytes(paths: list[Any]) -> int:
    """估算存储占用（字节）：给定路径列表求和文件大小（流水 + 向量合计）。"""
    total = 0
    for p in paths:
        try:
            total += int(Path(p).stat().st_size)
        except OSError:
            pass
    return total


def threshold_state(used_bytes: int, budget_bytes: int) -> str:
    """占用阈值状态：'ok' | 'warn'(80%) | 'critical'(95%)。budget<=0 视为 ok（未设预算）。"""
    if budget_bytes <= 0:
        return "ok"
    ratio = float(used_bytes) / float(budget_bytes)
    if ratio >= THRESHOLD_CRITICAL:
        return "critical"
    if ratio >= THRESHOLD_WARN:
        return "warn"
    return "ok"


def _record_bytes(rec: dict[str, Any], dim: int) -> int:
    """单条 active 记录的估算字节：embedding(dim*4 float32) + text(utf-8)。"""
    emb = int(dim) * 4
    text = len(str(rec.get("text", "")).encode("utf-8"))
    return emb + text


def _day_of(ts: object) -> str:
    try:
        return _dt.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return "1970-01-01"


def _to_int(v: object, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def default_long_window_days() -> int:
    return _to_int(config.get("memory.long_window_days", 3650), 3650) or 3650


def default_short_window_days() -> int:
    return _to_int(config.get("memory.short_window_days", 730), 730) or 730


def resolve_budget_mb() -> int:
    """解析存储预算（MB）：档位值或「自定义」→ custom 值；默认 300。"""
    v = config.get("memory.storage_budget_mb", 300)
    s = str(v).strip().lower()
    if s == "custom":
        return _to_int(config.get("memory.storage_budget_custom_mb", 0), 300) or 300
    return _to_int(v, 300) or 300


__all__ = [
    "P0_KINDS",
    "THRESHOLD_WARN",
    "THRESHOLD_CRITICAL",
    "is_p0",
    "summary_pointer",
    "enforce_vector_retention",
    "enforce_capacity",
    "storage_usage_bytes",
    "threshold_state",
    "default_long_window_days",
    "default_short_window_days",
    "resolve_budget_mb",
]
