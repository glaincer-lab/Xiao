"""M1 向量存储层（backend/memv1/vector_store.py）单元测试。

覆盖：numpy 兜底实现的 upsert/query/invalidate/delete/rebuild/count/持久化，
以及工厂降级（本机未装 sqlite-vec → 返回 NumpyVectorStore）。

注：落盘临时目录用 ROOT/.tmp 下 makedirs 子目录（而非 tempfile）——本仓库在 DSH
沙箱内运行时 tempfile 生成目录（系统 Temp）被拒写，见 test_memv4.py 同款约定。
"""
from __future__ import annotations

import shutil
import time
import unittest
import uuid
from pathlib import Path

from backend.config import ROOT
from backend.memv1.vector_store import (
    NumpyVectorStore,
    SqliteVecStore,
    VectorRecord,
    get_vector_store,
    vector_store_available,
)


def _make_tmp_dir(name: str) -> Path:
    d = ROOT / ".tmp" / f"{name}_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rec(rid: str, text: str, emb: list[float], meta: dict | None = None, ts: float | None = None) -> VectorRecord:
    return VectorRecord(id=rid, text=text, embedding=emb, meta=meta or {}, ts=ts if ts is not None else time.time())


class NumpyVectorStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_tmp_dir("vector_store_test")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        self.store = NumpyVectorStore(self._tmp / "vec.json")

    def test_upsert_and_query_by_similarity(self) -> None:
        self.store.upsert(_rec("a", "我喜欢猫", [1.0, 0.0]))
        self.store.upsert(_rec("b", "我喜欢狗", [0.0, 1.0]))
        res = self.store.query([1.0, 0.1], top_k=1)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], "a")
        self.assertGreater(res[0]["score"], 0.9)

    def test_invalidate_clears_embedding_keeps_pointer(self) -> None:
        self.store.upsert(_rec("a", "原始内容", [1.0, 0.0]))
        self.store.invalidate("a", "一句摘要指针")
        self.assertEqual(self.store.active_count(), 0)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.query([1.0, 0.0], top_k=5), [])
        recs = self.store.all_records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["meta"]["status"], "invalidated")
        self.assertEqual(recs[0]["text"], "一句摘要指针")

    def test_delete_removes_physically(self) -> None:
        self.store.upsert(_rec("a", "x", [1.0, 0.0]))
        self.store.delete("a")
        self.assertEqual(self.store.count(), 0)
        self.assertEqual(self.store.all_records(), [])

    def test_rebuild_from_truth(self) -> None:
        self.store.upsert(_rec("old", "旧", [1.0, 0.0]))
        self.store.rebuild([_rec("new", "新", [0.0, 1.0])])
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.all_records()[0]["id"], "new")

    def test_persistence_roundtrip(self) -> None:
        self.store.upsert(_rec("a", "持久化", [1.0, 0.0]))
        store2 = NumpyVectorStore(self.store._path)
        self.assertEqual(store2.count(), 1)
        self.assertEqual(store2.all_records()[0]["id"], "a")


class FactoryTest(unittest.TestCase):
    def test_get_vector_store_matches_backend_availability(self) -> None:
        d = _make_tmp_dir("vector_store_factory")
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        store = get_vector_store(path=d / "vec.json")
        if vector_store_available():
            self.assertIsInstance(store, SqliteVecStore)
        else:
            self.assertIsInstance(store, NumpyVectorStore)
        store.upsert(_rec("a", "hello", [1.0, 0.0]))
        self.assertEqual(store.active_count(), 1)


if __name__ == "__main__":
    unittest.main()
