"""M1 向量存储层：sqlite-vec 主选 + numpy 暴力兜底（零依赖降级）。

向量条目是 M1 画像 + M6 共同记忆的「检索投影」（派生索引），**不是真源**：
真源仍是 M1 画像存储（ProfileStore）与 GrowthStore，向量库可由真源重建（rebuild）。

后端选型（老板定稿，见 M1-vector-memory.md §2/§8 问题4）：
- 主选：sqlite-vec 0.1.9（win_amd64 预编译 wheel，py3-none；许可证 MIT + Apache-2.0 双许可，与项目 MIT 兼容，已核验）
- 兜底：numpy 暴力 cosine 检索（零新增依赖，开箱即用）

工厂 get_vector_store 探测失败时自动回退 NumpyVectorStore——设计书 §4.4「sqlite-vec 不可用 →
numpy 暴力检索」。SqliteVecStore 已在本机真机验证（2026-08-31，sqlite-vec 0.1.9）：
建表/upsert/查询/invalidate/delete/rebuild 全链路通过。验证修正两处与官方 API 的差异：
① KNN 查询需 `k = ?` 约束（JOIN 场景 LIMIT 不被识别）；② vec0 默认 L2 距离，需显式
`distance_metric=cosine` 以对齐 numpy 兜底的余弦相似度语义。

字段约定（对齐设计书 §3.1）：VectorRecord = {id, text, embedding, meta, ts}，
meta 含 kind/scope/effective_at/source/status/confidence/importance。
失效标记：status→invalidated + 清空 embedding + text→摘要指针（可回溯，非物理删除）。
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from backend.config import ROOT

# embedding 维度（bge-small-zh-v1.5，写死，见设计书 §5）
DIM = 512

# sqlite-vec 可选探测（未装则回退 numpy，绝不因缺后端而无法启动）
try:
    import sqlite_vec  # type: ignore

    _SQLITE_VEC_AVAILABLE = True
except Exception:  # noqa: BLE001
    sqlite_vec = None
    _SQLITE_VEC_AVAILABLE = False


@dataclass
class VectorRecord:
    """向量条目（长期层）。meta 字段对齐设计书 §3.1。"""

    id: str
    text: str
    embedding: list[float]
    meta: dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VectorStore:
    """向量存储抽象接口（sqlite-vec 与 numpy 两实现共用）。"""

    def upsert(self, rec: VectorRecord) -> None:
        raise NotImplementedError

    def query(self, qvec: list[float], top_k: int) -> list[dict[str, Any]]:
        """返回 active 条目中最相似的 top_k 条：[{id,text,meta,ts,score}]。score=cosine。"""
        raise NotImplementedError

    def invalidate(self, rec_id: str, summary_pointer: str) -> None:
        """失效标记：status→invalidated + 清空 embedding + text→摘要指针（非物理删除）。"""
        raise NotImplementedError

    def delete(self, rec_id: str) -> None:
        """物理删除（用户手动 forget 走这里）。"""
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError

    def active_count(self) -> int:
        raise NotImplementedError

    def all_records(self) -> list[dict[str, Any]]:
        """全部记录 [{id,text,meta,ts}]（含 invalidated，供治理层遍历）。"""
        raise NotImplementedError

    def rebuild(self, records: list[VectorRecord]) -> None:
        """清空并按 records 重建索引（向量库损坏/删库后靠真源重建）。"""
        raise NotImplementedError


class NumpyVectorStore(VectorStore):
    """numpy 暴力 cosine 检索（零依赖兜底）。内存矩阵 + JSON 原子落盘。"""

    def __init__(self, path: str | Path | None = None, dim: int = DIM) -> None:
        self._dim = int(dim)
        self._path = Path(path) if path is not None else ROOT / "logs" / "memv1" / "vector_store.json"
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        self._load()

    # ---- 持久化 ----

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            data = {"records": []}
        self._records = {}
        for r in data.get("records", []) if isinstance(data, dict) else []:
            if isinstance(r, dict) and r.get("id") and isinstance(r.get("embedding"), list):
                self._records[str(r["id"])] = r

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            payload = {"records": list(self._records.values())}
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:  # noqa: BLE001
            print(f"[vector_store] numpy 落盘失败: {exc}")

    # ---- 接口 ----

    def upsert(self, rec: VectorRecord) -> None:
        meta = dict(rec.meta or {})
        meta.setdefault("status", "active")
        ts = rec.ts if rec.ts > 0 else time.time()
        with self._lock:
            self._records[str(rec.id)] = {
                "id": str(rec.id),
                "text": str(rec.text or ""),
                "embedding": [float(x) for x in rec.embedding],
                "meta": meta,
                "ts": float(ts),
            }
            self._save()

    def query(self, qvec: list[float], top_k: int) -> list[dict[str, Any]]:
        q = np.asarray(qvec, dtype=np.float32)
        qn = float(np.linalg.norm(q))
        with self._lock:
            items = [dict(r) for r in self._records.values()]
        if not items or qn == 0:
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for r in items:
            if r.get("meta", {}).get("status") == "invalidated":
                continue
            emb = np.asarray(r.get("embedding", []), dtype=np.float32)
            if emb.size == 0:
                continue
            denom = float(np.linalg.norm(emb))
            if denom == 0:
                continue
            sim = float(np.dot(q, emb) / (qn * denom))
            scored.append((sim, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        out: list[dict[str, Any]] = []
        for sim, r in scored[: max(1, int(top_k))]:
            out.append({
                "id": r.get("id"),
                "text": r.get("text", ""),
                "meta": r.get("meta", {}),
                "ts": r.get("ts", 0.0),
                "score": sim,
            })
        return out

    def invalidate(self, rec_id: str, summary_pointer: str) -> None:
        with self._lock:
            r = self._records.get(str(rec_id))
            if r is None:
                return
            meta = dict(r.get("meta", {}))
            meta["status"] = "invalidated"
            r["meta"] = meta
            r["embedding"] = []  # 清空释放向量空间
            r["text"] = str(summary_pointer or "")  # 摘要指针可回溯
            self._save()

    def delete(self, rec_id: str) -> None:
        with self._lock:
            if str(rec_id) in self._records:
                del self._records[str(rec_id)]
                self._save()

    def count(self) -> int:
        with self._lock:
            return len(self._records)

    def active_count(self) -> int:
        with self._lock:
            return sum(
                1 for r in self._records.values()
                if r.get("meta", {}).get("status") != "invalidated"
            )

    def all_records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"id": r.get("id"), "text": r.get("text", ""), "meta": r.get("meta", {}), "ts": r.get("ts", 0.0)}
                for r in self._records.values()
            ]

    def rebuild(self, records: list[VectorRecord]) -> None:
        with self._lock:
            self._records = {}
            for rec in records:
                self._records[str(rec.id)] = {
                    "id": str(rec.id),
                    "text": str(rec.text or ""),
                    "embedding": [float(x) for x in rec.embedding],
                    "meta": dict(rec.meta or {}),
                    "ts": float(rec.ts),
                }
            self._save()


class SqliteVecStore(VectorStore):
    """sqlite-vec 主选后端（vec0 虚拟表 + rowid 关联元数据表）。

    已在本机真机验证（2026-08-31，sqlite-vec 0.1.9）：建表/upsert/查询/invalidate/
    delete/rebuild 全链路通过。关键 API 对齐：vec0 建表需显式 distance_metric=cosine
    （默认 L2），KNN 查询需 `AND k = ?` 约束（非 LIMIT）。
    """

    def __init__(self, path: str | Path | None = None, dim: int = DIM) -> None:
        import sqlite3

        self._dim = int(dim)
        self._path = Path(path) if path is not None else ROOT / "logs" / "memv1" / "vector_store.sqlite3"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)  # type: ignore[union-attr]
        self._conn.enable_load_extension(False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS memories("
            "rowid INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, text TEXT, meta TEXT, ts REAL)"
        )
        self._conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec USING vec0(embedding float[{self._dim}] distance_metric=cosine)"
        )
        self._conn.commit()

    def upsert(self, rec: VectorRecord) -> None:
        meta = dict(rec.meta or {})
        meta.setdefault("status", "active")
        ts = rec.ts if rec.ts > 0 else time.time()
        emb_json = json.dumps([float(x) for x in rec.embedding])
        meta_json = json.dumps(meta, ensure_ascii=False)
        with self._lock:
            row = self._conn.execute("SELECT rowid FROM memories WHERE id=?", (str(rec.id),)).fetchone()
            if row:
                rid = row[0]
                self._conn.execute(
                    "UPDATE memories SET text=?, meta=?, ts=? WHERE rowid=?",
                    (str(rec.text or ""), meta_json, float(ts), rid),
                )
                self._conn.execute("DELETE FROM memories_vec WHERE rowid=?", (rid,))
                self._conn.execute(
                    "INSERT INTO memories_vec(rowid, embedding) VALUES (?, ?)", (rid, emb_json)
                )
            else:
                cur = self._conn.execute(
                    "INSERT INTO memories(id, text, meta, ts) VALUES (?,?,?,?)",
                    (str(rec.id), str(rec.text or ""), meta_json, float(ts)),
                )
                rid = cur.lastrowid
                self._conn.execute(
                    "INSERT INTO memories_vec(rowid, embedding) VALUES (?, ?)", (rid, emb_json)
                )
            self._conn.commit()

    def query(self, qvec: list[float], top_k: int) -> list[dict[str, Any]]:
        q_json = json.dumps([float(x) for x in qvec])
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.id, m.text, m.meta, m.ts, v.distance "
                "FROM memories_vec v JOIN memories m ON m.rowid=v.rowid "
                "WHERE v.embedding MATCH ? AND k = ? ORDER BY v.distance",
                (q_json, max(1, int(top_k))),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for rid, text, meta_json, ts, distance in rows:
            try:
                meta = json.loads(meta_json) if meta_json else {}
            except json.JSONDecodeError:
                meta = {}
            if meta.get("status") == "invalidated":
                continue
            out.append({
                "id": rid,
                "text": text or "",
                "meta": meta,
                "ts": float(ts or 0.0),
                "score": 1.0 - float(distance),  # cosine distance → similarity
            })
        return out

    def invalidate(self, rec_id: str, summary_pointer: str) -> None:
        with self._lock:
            row = self._conn.execute("SELECT rowid, meta FROM memories WHERE id=?", (str(rec_id),)).fetchone()
            if not row:
                return
            rid = row[0]
            try:
                meta = json.loads(row[1]) if row[1] else {}
            except json.JSONDecodeError:
                meta = {}
            meta["status"] = "invalidated"
            self._conn.execute(
                "UPDATE memories SET meta=?, text=? WHERE rowid=?",
                (json.dumps(meta, ensure_ascii=False), str(summary_pointer or ""), rid),
            )
            self._conn.execute("DELETE FROM memories_vec WHERE rowid=?", (rid,))
            self._conn.commit()

    def delete(self, rec_id: str) -> None:
        with self._lock:
            row = self._conn.execute("SELECT rowid FROM memories WHERE id=?", (str(rec_id),)).fetchone()
            if row:
                self._conn.execute("DELETE FROM memories_vec WHERE rowid=?", (row[0],))
                self._conn.execute("DELETE FROM memories WHERE rowid=?", (row[0],))
                self._conn.commit()

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])

    def active_count(self) -> int:
        with self._lock:
            rows = self._conn.execute("SELECT meta FROM memories").fetchall()
        n = 0
        for (meta_json,) in rows:
            try:
                meta = json.loads(meta_json) if meta_json else {}
            except json.JSONDecodeError:
                meta = {}
            if meta.get("status") != "invalidated":
                n += 1
        return n

    def all_records(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT id, text, meta, ts FROM memories").fetchall()
        out: list[dict[str, Any]] = []
        for rid, text, meta_json, ts in rows:
            try:
                meta = json.loads(meta_json) if meta_json else {}
            except json.JSONDecodeError:
                meta = {}
            out.append({"id": rid, "text": text or "", "meta": meta, "ts": float(ts or 0.0)})
        return out

    def rebuild(self, records: list[VectorRecord]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM memories_vec")
            self._conn.execute("DELETE FROM memories")
            for rec in records:
                meta = dict(rec.meta or {})
                meta.setdefault("status", "active")
                ts = rec.ts if rec.ts > 0 else time.time()
                cur = self._conn.execute(
                    "INSERT INTO memories(id, text, meta, ts) VALUES (?,?,?,?)",
                    (str(rec.id), str(rec.text or ""), json.dumps(meta, ensure_ascii=False), float(ts)),
                )
                self._conn.execute(
                    "INSERT INTO memories_vec(rowid, embedding) VALUES (?, ?)",
                    (cur.lastrowid, json.dumps([float(x) for x in rec.embedding])),
                )
            self._conn.commit()


def vector_store_available() -> bool:
    """sqlite-vec 是否可用（探测结果）。"""
    return _SQLITE_VEC_AVAILABLE


def get_vector_store(path: str | Path | None = None, dim: int = DIM) -> VectorStore:
    """工厂：sqlite-vec 可用 → SqliteVecStore，否则 NumpyVectorStore（零依赖兜底）。"""
    if _SQLITE_VEC_AVAILABLE:
        try:
            return SqliteVecStore(path, dim)
        except Exception as exc:  # noqa: BLE001
            print(f"[vector_store] sqlite-vec 初始化失败，回退 numpy：{exc}")
    return NumpyVectorStore(path, dim)


def make_retriever(
    store: VectorStore,
    cfg: dict[str, Any] | None = None,
    candidate_k: int = 32,
) -> Callable[[str], list[dict[str, Any]] | None]:
    """构造向量召回器：query 文本 → 候选列表 [{id,text,meta,ts,score}]。

    供 retrieval.set_vector_retriever 接线；encode 失败返回 None（触发全量注入降级）。
    candidate_k 应大于最终 Top-K，先粗召回再四因子精排（设计书 §4.2）。
    """
    from backend.gateway import semantic_filter

    def retriever(query: str) -> list[dict[str, Any]] | None:
        try:
            qvec = semantic_filter.encode(query, cfg)
        except Exception:  # noqa: BLE001 - embedding 不可用 → 降级全量注入
            return None
        return store.query(qvec, max(1, int(candidate_k)))

    return retriever


__all__ = [
    "DIM",
    "VectorRecord",
    "VectorStore",
    "NumpyVectorStore",
    "SqliteVecStore",
    "vector_store_available",
    "get_vector_store",
    "make_retriever",
]
