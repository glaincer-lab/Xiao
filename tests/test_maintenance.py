"""M1 记忆后台管家（backend/memv1/maintenance.py）单元测试。"""
from __future__ import annotations

import shutil
import time
import unittest
import uuid
from pathlib import Path

import backend.memv1.maintenance as m
from backend.config import ROOT
from backend.m6.growth import GrowthStore
from backend.memv1.vector_store import NumpyVectorStore, VectorRecord


def _make_tmp_dir(name: str) -> Path:
    d = ROOT / ".tmp" / f"{name}_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rec(rid: str, text: str, ts: float, kind: str | None = None) -> VectorRecord:
    meta = {"kind": kind} if kind else {}
    return VectorRecord(id=rid, text=text, embedding=[1.0, 0.0], meta=meta, ts=ts)


class SweepNowTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_tmp_dir("maint_sweep")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        self.store = NumpyVectorStore(self._tmp / "vec.json")
        self.now = time.time()

    def test_invalidates_expired_keeps_p0(self) -> None:
        self.store.upsert(_rec("old", "旧", self.now - 4000 * 86400, kind="episodic"))
        self.store.upsert(_rec("p0", "共同", self.now - 4000 * 86400, kind="milestone"))
        r = m.sweep_now(store=self.store, long_window_days=3650, budget_mb=300)
        self.assertEqual(r["invalidated"], 1)
        self.assertIn(r["threshold"], ("ok", "warn", "critical"))


class IndexNowTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_tmp_dir("maint_index")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        self.store = NumpyVectorStore(self._tmp / "vec.json")
        self.gs = GrowthStore(self._tmp)

    def test_indexes_profile_and_growth(self) -> None:
        self.gs.add_shared_memory("共同")
        profile = [{"id": "p1", "content": "画像", "effective_at": "2026-01-01", "status": "active", "confidence": 0.7}]
        r = m.index_now(store=self.store, profile_entries=profile, growth_store=self.gs, encode_fn=lambda t: [1.0, 0.5, 0.0])
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["indexed"], 2)

    def test_encode_failure_tolerated(self) -> None:
        profile = [{"id": "p1", "content": "x", "status": "active"}]

        def boom(t: str):
            raise RuntimeError("no model")

        r = m.index_now(store=self.store, profile_entries=profile, growth_store=self.gs, encode_fn=boom)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["indexed"], 0)
        self.assertEqual(r["errors"], 1)


class ThrottleTest(unittest.TestCase):
    def test_should_consolidate_throttles(self) -> None:
        m._last_consolidation = 0.0
        self.assertTrue(m._should_consolidate(now=1000.0))
        self.assertFalse(m._should_consolidate(now=1000.0))
        self.assertTrue(m._should_consolidate(now=1000.0 + m.IDLE_CONSOLIDATION_SECONDS))


if __name__ == "__main__":
    unittest.main()
