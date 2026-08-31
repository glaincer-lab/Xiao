"""M1 记忆后台管家（Sleeptime 模式）：对话结束触发维护 + 定时兜底。

对应设计书 §4.1「对话结束 → 后台异步巩固 → 索引」+ §4.3「后台触发式治理」。
调度形态（老板定稿）：**聊完天触发 + 空闲消化，定时只做兜底**。

- run_after_turn()：每次对话结束调用，起 daemon 线程异步跑（不阻塞对话）：
  1. 索引（画像 + 三轨 → 向量，本地、无 LLM）
  2. 治理（失效标记 + 容量阈值检测，本地）
  3. 巩固（空闲满 15 分钟才触发，调云 LLM，节流避免每轮对话都提炼）
- start_sweeper()：定时兜底（daemon，长间隔做治理），幂等，防极端漏网。

本模块只做「调度 + 编排」，具体逻辑复用 indexer / governance / consolidate。
"""
from __future__ import annotations

import threading
import time
from typing import Any

# 巩固节流：空闲多久才重新巩固（对齐 consolidate.IDLE_CONSOLIDATION_SECONDS 语义）
IDLE_CONSOLIDATION_SECONDS = 15 * 60
# 定时兜底间隔（默认 6 小时）
DEFAULT_SWEEP_INTERVAL_SEC = 6 * 3600

_lock = threading.Lock()
_last_consolidation = 0.0
_sweeper_started = False
_last_threshold = "ok"  # 上次通知过的存储阈值状态（去重：只在变化时弹窗）


def index_now(
    store: Any = None,
    profile_entries: list | None = None,
    growth_store: Any = None,
    encode_fn: Any = None,
) -> dict[str, Any]:
    """热层索引：画像 + 三轨 → 向量。encode 缺失/失败 → 跳过索引（检索自动降级全量）。"""
    if encode_fn is None:
        try:
            from backend.gateway import semantic_filter

            encode_fn = semantic_filter.encode
        except Exception:  # noqa: BLE001
            return {"status": "no_embedding", "indexed": 0}
    if store is None:
        from backend.memv1.vector_store import get_vector_store

        store = get_vector_store()
    if profile_entries is None:
        try:
            from backend.memv1.consolidate import ProfileStore

            profile_entries = ProfileStore().load().get("entries", [])
        except Exception:  # noqa: BLE001
            profile_entries = []
    if growth_store is None:
        from backend.m6.growth import GrowthStore

        growth_store = GrowthStore()
    from backend.memv1.indexer import build_hot_index

    try:
        r = build_hot_index(store, profile_entries, growth_store, encode_fn)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)}
    return {"status": "ok", **r}


def sweep_now(
    store: Any = None,
    budget_mb: int | None = None,
    long_window_days: int | None = None,
) -> dict[str, Any]:
    """治理：时间窗口失效标记 + 容量阈值检测（不自动清理，仅报告状态）。"""
    from backend.memv1.governance import (
        default_long_window_days,
        enforce_vector_retention,
        resolve_budget_mb,
        storage_usage_bytes,
        threshold_state,
    )

    if store is None:
        from backend.memv1.vector_store import get_vector_store

        store = get_vector_store()
    long_days = long_window_days if long_window_days is not None else default_long_window_days()
    r = enforce_vector_retention(store, long_window_days=long_days)
    budget = budget_mb if budget_mb is not None else resolve_budget_mb()
    used = storage_usage_bytes(_store_files(store))
    budget_bytes = budget * 1024 * 1024
    return {
        "invalidated": r["invalidated"],
        "skipped_p0": r["skipped_p0"],
        "used_bytes": used,
        "budget_bytes": budget_bytes,
        "threshold": threshold_state(used, budget_bytes),
    }


def _sweep_and_notify() -> dict[str, Any]:
    """sweep_now + 阈值状态变化时 emit storage_threshold 事件（去重，避免每轮都弹）。"""
    global _last_threshold
    try:
        r = sweep_now()
    except Exception as exc:  # noqa: BLE001
        print(f"[maintenance] 治理检查失败: {exc}")
        return {"status": "error"}
    level = str(r.get("threshold", "ok"))
    with _lock:
        changed = level != _last_threshold
        _last_threshold = level
    if changed and level in ("warn", "critical"):
        try:
            from backend.session.state import emit

            emit(
                "storage_threshold",
                level=level,
                used_mb=int(r.get("used_bytes", 0) // (1024 * 1024)),
                budget_mb=int(r.get("budget_bytes", 0) // (1024 * 1024)),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[maintenance] 存储满事件发送失败: {exc}")
    return r


def clean_now() -> dict[str, Any]:
    """容量清理（用户确认后）：失效最旧的到回预算，最低保留最近 1 单元，P0 永不失效。"""
    from backend.memv1.governance import enforce_capacity, resolve_budget_mb
    from backend.memv1.vector_store import get_vector_store

    store = get_vector_store()
    budget = resolve_budget_mb()
    return enforce_capacity(store, budget_bytes=budget * 1024 * 1024)


def consolidate_if_idle(session_id: str = "xiao-main") -> dict[str, Any]:
    """空闲满 15 分钟才巩固（节流，调云 LLM）。返回 trigger_consolidation 的结果。"""
    if not _should_consolidate():
        return {"status": "throttled"}
    try:
        from backend.memv1.consolidate import trigger_consolidation

        return trigger_consolidation(session_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)}


def run_after_turn() -> None:
    """对话结束调用：daemon 线程异步跑 索引 → 治理 → 巩固（节流），不阻塞对话。"""
    def _job() -> None:
        for fn in (index_now, _sweep_and_notify, consolidate_if_idle):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                print(f"[maintenance] {fn.__name__} 失败: {exc}")

    threading.Thread(target=_job, daemon=True, name="mem-maintenance").start()


def start_sweeper(interval_sec: int | None = None) -> None:
    """启动定时兜底线程（daemon，长间隔做治理）。幂等（重复调用不重复起）。"""
    global _sweeper_started
    with _lock:
        if _sweeper_started:
            return
        _sweeper_started = True
    interval = interval_sec if interval_sec else DEFAULT_SWEEP_INTERVAL_SEC

    def _loop() -> None:
        while True:
            time.sleep(interval)
            try:
                _sweep_and_notify()
            except Exception as exc:  # noqa: BLE001
                print(f"[maintenance] 定时兜底失败: {exc}")

    threading.Thread(target=_loop, daemon=True, name="mem-sweeper").start()


def _should_consolidate(now: float | None = None) -> bool:
    """节流判断：距上次巩固 >= IDLE_CONSOLIDATION_SECONDS 才返回 True（并更新时间戳）。"""
    global _last_consolidation
    with _lock:
        t = now if now is not None else time.time()
        if t - _last_consolidation < IDLE_CONSOLIDATION_SECONDS:
            return False
        _last_consolidation = t
        return True


def _store_files(store: Any) -> list[str]:
    """估算存储占用的文件清单：向量库 + 流水三层 + 画像 + 成长记录。"""
    from pathlib import Path

    from backend.config import ROOT

    files: list[str] = []
    p = getattr(store, "_path", None)
    if p:
        files.append(str(p))
    base = ROOT / "logs"
    for rel in (
        "memv4/session_logs.json",
        "memv4/raw_frames_meta.json",
        "memv4/context_snapshots.json",
        "memv1_profile.json",
        "m6/growth.json",
    ):
        files.append(str(base / rel))
    return files


__all__ = [
    "IDLE_CONSOLIDATION_SECONDS",
    "DEFAULT_SWEEP_INTERVAL_SEC",
    "index_now",
    "sweep_now",
    "consolidate_if_idle",
    "clean_now",
    "run_after_turn",
    "start_sweeper",
]
