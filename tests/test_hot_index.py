"""M1 热层索引（backend/memv1/indexer.py）单元测试。"""
from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from backend.config import ROOT
from backend.m6.growth import GrowthStore
from backend.memv1.indexer import build_hot_index, date_ts, growth_records, profile_record
from backend.memv1.vector_store import NumpyVectorStore


def _mock_encode():
    return lambda text: [1.0, 0.5, 0.0]


def _make_tmp_dir(name: str) -> Path:
    d = ROOT / ".tmp" / f"{name}_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


class DateTsTest(unittest.TestCase):
    def test_iso_date(self) -> None:
        self.assertIsNotNone(date_ts("2026-01-01"))

    def test_invalid(self) -> None:
        self.assertIsNone(date_ts(""))
        self.assertIsNone(date_ts("not-a-date"))


class ProfileRecordTest(unittest.TestCase):
    def test_projects_fields(self) -> None:
        e = {"id": "p1", "content": "老板喜欢喝茶", "effective_at": "2026-01-01", "status": "active", "confidence": 0.9}
        rec = profile_record(e, _mock_encode())
        self.assertIsNotNone(rec)
        self.assertEqual(rec.text, "老板喜欢喝茶")
        self.assertEqual(rec.meta["kind"], "episodic")
        self.assertAlmostEqual(rec.meta["importance"], 0.9)
        self.assertEqual(rec.meta["status"], "active")

    def test_skips_expired(self) -> None:
        e = {"id": "p1", "content": "x", "status": "expired"}
        self.assertIsNone(profile_record(e, _mock_encode()))


class GrowthRecordsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_tmp_dir("hot_growth")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        self.gs = GrowthStore(self._tmp)

    def test_projects_p0(self) -> None:
        self.gs.add_user_record("完成大项目", source="explicit")
        self.gs.add_shared_memory("一起旅行", luminance=5)
        recs = growth_records(self.gs, _mock_encode())
        self.assertEqual(len(recs), 2)
        for r in recs:
            self.assertTrue(r.meta["p0"])
            self.assertEqual(r.meta["importance"], 1.0)
            self.assertIn(r.meta["kind"], ("milestone", "person"))


class BuildHotIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_tmp_dir("hot_build")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        self.store = NumpyVectorStore(self._tmp / "vec.json")
        self.gs = GrowthStore(self._tmp)

    def test_rebuild_indexes_profile_and_growth(self) -> None:
        self.gs.add_shared_memory("共同记忆")
        profile = [{"id": "p1", "content": "画像记忆", "effective_at": "2026-01-01", "status": "active", "confidence": 0.7}]
        r = build_hot_index(self.store, profile, self.gs, _mock_encode())
        self.assertEqual(r["indexed"], 2)
        self.assertEqual(self.store.count(), 2)

    def test_encode_failure_counted(self) -> None:
        profile = [{"id": "p1", "content": "x", "status": "active"}]

        def boom(text: str):
            raise RuntimeError("no model")

        r = build_hot_index(self.store, profile, self.gs, boom)
        self.assertEqual(r["errors"], 1)
        self.assertEqual(self.store.count(), 0)


if __name__ == "__main__":
    unittest.main()
