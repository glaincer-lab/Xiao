"""M1 热层索引：把真源（画像 + 共同记忆 + 成长三轨）投影为向量条目（派生索引）。

向量库是派生索引，可由真源重建（设计书 §3.3）。本模块提供「真源 → VectorRecord」的
投影 + encode，供后台巩固流水线增量 upsert / 一键重建调用。encode_fn 注入（测试用 mock，
生产用 semantic_filter.encode）。

P0 约定（设计书 §4.3）：成长三轨（user_track/agent_track）与共同记忆（shared_memories）
恒高重要度、永不失效——投影时 kind=milestone/person + p0=True + importance=1.0。
普通画像条目 kind=episodic。
"""
from __future__ import annotations

import datetime as _dt
import time
from typing import Any, Callable

from backend.memv1.vector_store import VectorRecord

# encode_fn: text → list[float]（512 维）；失败抛异常（调用方跳过/计数）
EncodeFn = Callable[[str], list[float]]


def date_ts(value: object) -> float | None:
    """ISO date/datetime 或 epoch → epoch 秒；解析失败返回 None。"""
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return _dt.datetime.strptime(s, fmt).timestamp()
        except (ValueError, TypeError):
            continue
    try:
        return _dt.datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return None


def _get(entry: object, key: str, default: object = None) -> object:
    if isinstance(entry, dict):
        return entry.get(key, default)
    return getattr(entry, key, default)


def profile_record(entry: object, encode_fn: EncodeFn) -> VectorRecord | None:
    """画像条目 → VectorRecord；content 空 / status ∈ {expired, invalidated} → None。"""
    text = str(_get(entry, "content", "") or "").strip()
    if not text:
        return None
    status = str(_get(entry, "status", "active") or "active")
    if status in ("expired", "invalidated"):
        return None
    emb = encode_fn(text)
    ts = date_ts(_get(entry, "effective_at", ""))
    if ts is None:
        ts = time.time()
    try:
        confidence = float(_get(entry, "confidence", 0.5) or 0.5)
    except (TypeError, ValueError):
        confidence = 0.5
    meta = {
        "kind": "episodic",
        "effective_at": str(_get(entry, "effective_at", "") or ""),
        "source": str(_get(entry, "source", "explicit") or "explicit"),
        "status": status,
        "confidence": confidence,
        "importance": max(0.0, min(1.0, confidence)),
        "affective_luminance": _get(entry, "affective_luminance", 0) or 0,
    }
    return VectorRecord(id=str(_get(entry, "id", "") or ""), text=text, embedding=emb, meta=meta, ts=ts)


def growth_records(growth_store: Any, encode_fn: EncodeFn) -> list[VectorRecord]:
    """GrowthStore 三轨 + 共同记忆 → VectorRecord（P0：永不失效、恒高重要度）。"""
    records: list[VectorRecord] = []

    def _emit(rid: object, text: str, ts: float, kind: str, extra: dict[str, Any] | None = None) -> None:
        t = str(text or "").strip()
        if not t:
            return
        try:
            emb = encode_fn(t)
        except Exception:  # noqa: BLE001
            return
        meta = {"kind": kind, "p0": True, "importance": 1.0}
        if extra:
            meta.update(extra)
        records.append(VectorRecord(id=str(rid or ""), text=t, embedding=emb, meta=meta, ts=ts))

    for r in growth_store.shared_memories() or []:
        _emit(r.get("id"), r.get("event"), float(r.get("ts", 0) or time.time()),
              "person", {"affective_luminance": r.get("luminance", 5)})
    for r in growth_store.user_records() or []:
        _emit(r.get("id"), r.get("milestone"), float(r.get("ts", 0) or time.time()), "milestone")
    for r in growth_store.agent_records() or []:
        _emit(r.get("id"), r.get("milestone"), float(r.get("ts", 0) or time.time()), "milestone")
    return records


def build_hot_index(
    store: Any,
    profile_entries: list[object],
    growth_store: Any,
    encode_fn: EncodeFn,
) -> dict[str, int]:
    """一键重建热层索引：清空 + 画像 + 成长三轨 + 共同记忆 → upsert。

    返回 {"indexed": 成功投影数, "errors": encode/投影失败跳过数}。用于删库后靠真源重建。
    """
    records: list[VectorRecord] = []
    errors = 0
    for e in profile_entries or []:
        try:
            rec = profile_record(e, encode_fn)
        except Exception:  # noqa: BLE001
            errors += 1
            continue
        if rec is not None:
            records.append(rec)
    try:
        records.extend(growth_records(growth_store, encode_fn))
    except Exception:  # noqa: BLE001
        pass
    store.rebuild(records)
    return {"indexed": len(records), "errors": errors}


__all__ = [
    "EncodeFn",
    "date_ts",
    "profile_record",
    "growth_records",
    "build_hot_index",
]
