"""backend.memv1.schema —— M1-A 契约的「薄转发层」。

把 M1-A 的正式实现（`backend/memv4.py`：MemEntry + DataTrack + evict_low_confidence）
re-export 到 `backend.memv1.schema` 命名空间，使两条 import 路径指向**同一份实现**：
    from backend.memv1.schema import MemEntry, DataTrack   # subagent 用（契约路径）
    from backend.memv4 import MemEntry, DataTrack          # M1-A 正式实现

本层只转发、不复制、不改动。若 M1-A 的实现目录或文件名变更，仅改本文件即可统一两边。
"""

from __future__ import annotations

from backend.memv4 import (
    DATA_TRACK_KINDS,
    PROFILE_MAX_ENTRIES,
    DataTrack,
    MemEntry,
    evict_low_confidence,
)

__all__ = [
    "MemEntry",
    "DataTrack",
    "evict_low_confidence",
    "DATA_TRACK_KINDS",
    "PROFILE_MAX_ENTRIES",
]
