"""M1-A 记忆基础层：五要素 schema + 数据轨三层存储（v4）。

与 `backend/memory.py`（v3 简化版，只存显式记忆）**并存且独立**：
- v3 `MemoryStore` 保留不动（现有 API 不破坏）；
- 本模块为 v4 升级，供 M1-B/C/D/E 消费。

本模块只做「数据模型 + 数据轨存储」这一基础层，**不含 LLM、不含槽位抽取、不含冲突协议**
（那些交给 M1-B~F），因此 **仅标准库**，依赖零新增。

# 数据轨道（100% 物理保留，写死）
三条**只增不删**的原始数据轨，来源于 M1-memory.md §3「存储分层」：
- `session_logs`      会话原文（永远追加，零删除）
- `raw_frames_meta`   帧元数据 + 文字结论（帧图像本体不入库，即焚）
- `context_snapshots` 上下文快照

这三轨配合 `MemEntry`（五要素画像条目）构成 M1 基础层；画像条目另有容量策略：
**上限 500（超限低置信淘汰）**，由 `evict_low_confidence` 实施。

# 加密预留（v4.1.1）
schema 预留 `encrypted` + `enc_token` 字段，首版不启用（DPAPI 本机绑定为候选），
避免 MVP 后积累敏感记忆再迁移方案的高昂成本。

接口契约（写死，供 M1-B~F 并行使用）：

    MemEntry                            # 五要素记忆条目 dataclass（字段名与 §3 完全一致）
    DataTrack.append(kind, payload) -> str   # kind ∈ session_logs/raw_frames_meta/context_snapshots；返回 id
    DataTrack.count(kind) -> int

所有层保证：任一操作后 `session_logs` 行数不减少（原始层零丢失断言）。

MIT。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from backend.config import ROOT

# 数据轨三种 kind（写死，不许扩展出自此范围外）
DATA_TRACK_KINDS: frozenset[str] = frozenset({
    "session_logs",
    "raw_frames_meta",
    "context_snapshots",
})

# 画像条目容量上限（M1-memory §3 容量策略：超限低置信淘汰）
PROFILE_MAX_ENTRIES = 500


# 【已冻结 · T0/S3】五要素 schema（MemEntry 字段集）为一次性写入、只增不改的非破坏结构。
# 字段一旦变更会导致历史数据不可读；本结构已按 M1-core §3 锁定，禁止改动既有字段名/增删核心字段。
@dataclass
class MemEntry:
    """五要素记忆条目 dataclass（字段名与 M1-memory.md §3 完全一致）。

    字段来自 §3 的 JSONC schema；`encrypted`/`enc_token` 为预留字段（首版不启用）。
    提供默认值以支持简单实例化与反序列化测试。
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    content: str = ""
    scope: str = "global"                 # global|period|occasion|event|place
    scope_detail: dict[str, Any] = field(default_factory=dict)  # 作用域参数（时段终点等）
    effective_at: str = ""                # 生效时间（ISO date）
    source: str = "explicit"              # explicit|behavior|inferred
    status: str = "active"                # active|locally_overridden|expired|pending_clarify
    confirmed: bool = False               # 听错分级：高风险复述确认后 true
    affective_luminance: int = 0          # 情感高光度 0-5（巩固层）
    confidence: float = 1.0               # 置信度 0-1（推断型随时间降权）
    # ---- 加密预留（v4.1.1，首版不启用）----
    encrypted: bool = False
    enc_token: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evict_low_confidence(
    entries: list["MemEntry"],
    max_entries: int = PROFILE_MAX_ENTRIES,
) -> tuple[list["MemEntry"], list["MemEntry"]]:
    """画像条目容量策略：超过 `max_entries` 时按置信度升序淘汰低置信条目。

    保证至少保留 `max_entries` 条；同置信度时优先淘汰未确认（confirmed=False）者。
    不修改入参列表（返回两份新列表），调用方据此提交持久化。

    Returns:
        (kept, removed)：保留的条目列表 与 被淘汰的条目列表。
    """
    max_entries = max(1, int(max_entries))
    if len(entries) <= max_entries:
        return list(entries), []
    ordered = sorted(
        entries,
        key=lambda e: (e.confidence, e.confirmed),  # 置信低在前；确认的重
    )
    kept = list(ordered[max_entries:])  # 置信高的留下
    removed = list(ordered[:max_entries])  # 置信低的优先淘汰
    # 稳定排序但保留原相对顺序（不强制重排调用方视角）；此处按 id 排序保证确定性
    return sorted(kept, key=lambda e: e.id), sorted(removed, key=lambda e: e.id)


# 【已冻结 · T0/S3】DataTrack 三层职责（session_logs/raw_frames_meta/context_snapshots）为只增不改的
# 非破坏存储结构；变更 kind 或职责会破坏「原始层零丢失」断言与历史可读性。
class DataTrack:
    """数据轨三层存储：只增不删、原子落盘、线程安全。

    每层一个 JSON 文件（`session_logs.json` / `raw_frames_meta.json` /
    `context_snapshots.json`），记录格式为 `[{"id","ts",**payload}, ...]`。
    `append` 只在层内追加，**不提供任何删除/清空 API**，从结构上保证原始层零丢失。

    落盘采用原子写（tmp + `os.replace`，仿照 `backend/memory.py` 的 `_save`）。
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = ROOT / "logs" / "memv4" if root is None else Path(root)
        self._path_by_kind = {k: self._root / f"{k}.json" for k in DATA_TRACK_KINDS}
        self._lock = threading.Lock()
        self._layers: dict[str, list[dict[str, Any]]] = {k: self._load(k) for k in DATA_TRACK_KINDS}

    # ---- 对外接口（契约）----

    def append(self, kind: str, payload: dict[str, Any] | None = None) -> str:
        """向指定数据轨追加一条记录；返回新记录 id。

        Args:
            kind: `session_logs` | `raw_frames_meta` | `context_snapshots`
            payload: 任意 dict；缺省为 `{}`。

        Raises:
            ValueError: kind 不在白名单（fail-fast，指向 DATA_TRACK_KINDS）。
            TypeError: payload 非 dict。
        """
        self._validate_kind(kind)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise TypeError(f"payload 必须是 dict，收到 {type(payload).__name__}")
        record = {"id": uuid.uuid4().hex[:12], "ts": time.time(), **payload}
        with self._lock:
            self._layers[kind].append(record)
            self._save(kind)
        return str(record["id"])

    def count(self, kind: str) -> int:
        """某数据轨当前记录行数。"""
        self._validate_kind(kind)
        with self._lock:
            return len(self._layers[kind])

    # ---- 读取（供后续消费与测试/重载校验；只读，不影响零丢失）----

    def items(self, kind: str) -> list[dict[str, Any]]:
        """某数据轨全部记录（每条为拷贝，外部改动不影响内存态）。"""
        self._validate_kind(kind)
        with self._lock:
            return [dict(r) for r in self._layers[kind]]

    # ---- 内部 ----

    @staticmethod
    def _validate_kind(kind: str) -> None:
        if kind not in DATA_TRACK_KINDS:
            raise ValueError(
                f"未知数据轨 kind：{kind!r}。只允许 {sorted(DATA_TRACK_KINDS)}"
                "（见 M1-memory.md §3 数据轨分层，不得自行扩展）。"
            )

    def _load(self, kind: str) -> list[dict[str, Any]]:
        """从磁盘重载该层记录；文件缺失 / 损坏时重置为空，不让坏文件卡死。"""
        path = self._path_by_kind[kind]
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict)]
            return []
        except FileNotFoundError:
            return []
        except Exception:  # noqa: BLE001
            print(f"[memv4] 数据轨文件损坏，已重置为空: {path}")
            return []

    def _save(self, kind: str) -> None:
        """原子写：先写临时文件，再 os.replace 覆盖目标（与 memory.py 同模式）。"""
        path = self._path_by_kind[kind]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self._layers[kind], f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except OSError as e:
            print(f"[memv4] 数据轨保存失败 ({kind}): {e}")


__all__ = [
    "DATA_TRACK_KINDS",
    "PROFILE_MAX_ENTRIES",
    "MemEntry",
    "DataTrack",
    "evict_low_confidence",
]
