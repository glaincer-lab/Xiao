"""M4-M3 视线对齐：VLM 结构化返回的"位置"字段 → 星云偏移/高亮信号。

纯渲染增强：本模块只计算"位置 → 归一化偏移"，不改星云核心（Nebula.tsx）。
前端消费该偏移做视觉偏移/高亮渲染（视觉方向待前端确认，与 M2-E 星云映射同后置）。
"""
from __future__ import annotations

from typing import Any, Mapping


def _clamp(v: float) -> float:
    return max(-1.0, min(1.0, v))


def gaze_offset(payload: Mapping[str, Any]) -> dict[str, float] | None:
    """从 VLM payload 解析"位置"字段 → 归一化偏移 {x,y} ∈ [-1,1]。

    - 有位置字段 → 返回偏移（供前端偏移渲染）
    - 无位置字段 → None（默认星云形态，不偏移）
    """
    pos = payload.get("位置") if isinstance(payload, Mapping) else None
    if pos is None:
        pos = payload.get("position") if isinstance(payload, Mapping) else None
    if pos is None:
        return None
    # 支持 dict {x,y} 或 list/tuple [x,y]
    if isinstance(pos, Mapping):
        x, y = pos.get("x"), pos.get("y")
    elif isinstance(pos, (list, tuple)) and len(pos) >= 2:
        x, y = pos[0], pos[1]
    else:
        return None
    try:
        fx, fy = float(x), float(y)
    except (TypeError, ValueError):
        return None
    return {"x": _clamp(fx), "y": _clamp(fy)}


__all__ = ["gaze_offset"]
