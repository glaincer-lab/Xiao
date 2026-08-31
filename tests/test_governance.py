"""M1 存储治理（backend/memv1/governance.py）单元测试。

覆盖：P0 判定、摘要指针、时间窗口失效、容量上限清理（最低保留最近 1 单元 + 不切碎 +
P0 永不失效）、存储满阈值、DataTrack 短期清理。
仅标准库 + numpy（复用 NumpyVectorStore 作被测 store）。
"""
from __future__ import annotations

import shutil
import time
import unittest
import uuid
from pathlib import Path

from backend.config import ROOT
from backend.memv1.governance import (
    enforce_capacity,
    enforce_vector_retention,
    is_p0,
    summary_pointer,
    threshold_state,
)
from backend.memv1.vector_store import NumpyVectorStore, VectorRecord
from backend.memv4 import DataTrack


def _make_tmp_dir(name: str) -> Path:
    d = ROOT / ".tmp" / f"{name}_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rec(rid: str, text: str, ts: float, kind: str | None = None, p0: bool = False) -> VectorRecord:
    meta: dict = {"kind": kind} if kind else {}
    if p0:
        meta["p0"] = True
    return VectorRecord(id=rid, text=text, embedding=[1.0, 0.0], meta=meta, ts=ts)


class P0Test(unittest.TestCase):
    def test_p0_by_kind(self) -> None:
        self.assertTrue(is_p0({"kind": "person"}))
        self.assertTrue(is_p0({"kind": "milestone"}))
        self.assertFalse(is_p0({"kind": "episodic"}))

    def test_p0_by_flag(self) -> None:
        self.assertTrue(is_p0({"p0": True}))
        self.assertFalse(is_p0({}))
        self.assertFalse(is_p0("not-a-dict"))


class SummaryPointerTest(unittest.TestCase):
    def test_truncates_and_marks(self) -> None:
        p = summary_pointer("这是一条很长的记忆内容" * 10)
        self.assertIn("仅留印记", p)
        self.assertLessEqual(len(p.split("（细节已随超长期淡忘，仅留印记）")[0]), 61)

    def test_empty_text(self) -> None:
        self.assertIn("仅留印记", summary_pointer(""))


class VectorRetentionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_tmp_dir("gov_retention")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        self.store = NumpyVectorStore(self._tmp / "vec.json")
        self.now = time.time()
        self.old = self.now - 4000 * 86400  # 超 3650 天
        self.fresh = self.now - 100 * 86400  # 未超窗口

    def test_expired_non_p0_invalidated_p0_kept(self) -> None:
        self.store.upsert(_rec("expired", "旧记忆", self.old, kind="episodic"))
        self.store.upsert(_rec("p0", "共同记忆", self.old, kind="milestone"))
        self.store.upsert(_rec("fresh", "新记忆", self.fresh, kind="episodic"))
        r = enforce_vector_retention(self.store, long_window_days=3650, now_ts=self.now)
        self.assertEqual(r["invalidated"], 1)
        self.assertEqual(r["skipped_p0"], 1)
        self.assertEqual(self.store.active_count(), 2)  # p0 + fresh 保留


class CapacityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_tmp_dir("gov_capacity")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        self.store = NumpyVectorStore(self._tmp / "vec.json")
        self.now = time.time()
        self.day1 = self.now - 3 * 86400  # 最旧单元
        self.day2 = self.now - 2 * 86400
        self.day3 = self.now - 1 * 86400  # 最近单元

    def test_capacity_keeps_latest_unit_and_p0(self) -> None:
        self.store.upsert(_rec("d1a", "旧1", self.day1))
        self.store.upsert(_rec("d1b", "旧2", self.day1))
        self.store.upsert(_rec("d1p0", "旧P0", self.day1, kind="person"))
        self.store.upsert(_rec("d2a", "中1", self.day2))
        self.store.upsert(_rec("d3a", "新1", self.day3))
        r = enforce_capacity(self.store, budget_bytes=0, now_ts=self.now, dim=2)
        # 最旧单元(day1 非P0) 与 中间单元(day2) 失效；最近单元(day3) 与 P0 保留
        self.assertEqual(r["invalidated"], 3)  # d1a + d1b + d2a
        self.assertEqual(r["skipped_p0"], 1)  # d1p0
        active_ids = {rec["id"] for rec in self.store.all_records() if rec["meta"]["status"] != "invalidated"}
        self.assertIn("d3a", active_ids)
        self.assertIn("d1p0", active_ids)
        self.assertNotIn("d1a", active_ids)
        self.assertNotIn("d2a", active_ids)


class ThresholdTest(unittest.TestCase):
    def test_thresholds(self) -> None:
        self.assertEqual(threshold_state(75, 100), "ok")
        self.assertEqual(threshold_state(85, 100), "warn")
        self.assertEqual(threshold_state(96, 100), "critical")
        self.assertEqual(threshold_state(100, 0), "ok")  # 未设预算


class DataTrackPruneTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_tmp_dir("gov_datatrack")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        self.dt = DataTrack(self._tmp)
        self.now = time.time()

    def test_prune_before_removes_old_keeps_recent(self) -> None:
        self.dt.append("session_logs", {"text": "旧", "ts": self.now - 800 * 86400})
        self.dt.append("session_logs", {"text": "新", "ts": self.now})
        removed = self.dt.prune_before(self.now - 730 * 86400)
        self.assertEqual(removed, 1)
        self.assertEqual(self.dt.count("session_logs"), 1)


if __name__ == "__main__":
    unittest.main()
