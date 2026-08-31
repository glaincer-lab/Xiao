"""M6-M0 GrowthStore 数据层测试：双轨分离、字段完整、原子落盘、冷却往返、容错。"""
from __future__ import annotations

import os
import shutil
import unittest
import uuid
from pathlib import Path

from backend.config import ROOT
from backend.m6.growth import GrowthStore


def _make_tmp_dir() -> Path:
    """在 workspace 的 .tmp 下创建可写临时目录（沙箱拒写系统 Temp，见 test_memv4.py）。"""
    d = ROOT / ".tmp" / f"m6_test_{uuid.uuid4().hex[:8]}"
    os.makedirs(d, exist_ok=True)
    return d


class GrowthStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_tmp_dir()
        self._root = self._tmp
        self.store = GrowthStore(root=self._root)
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))

    def test_user_and_agent_tracks_separate(self) -> None:
        self.store.add_user_record("项目拿下", canon=True)
        self.store.add_agent_record("第一次自动调温", capability_event="ha_scene_auto")
        self.assertEqual(len(self.store.user_records()), 1)
        self.assertEqual(len(self.store.agent_records()), 1)
        # 双轨分离：用户轨不含 capability_event，小二轨不含 source
        self.assertNotIn("capability_event", self.store.user_records()[0])
        self.assertNotIn("source", self.store.agent_records()[0])

    def test_user_record_fields(self) -> None:
        r = self.store.add_user_record("升职", source="行为推断", canon=True)
        self.assertEqual(r["milestone"], "升职")
        self.assertEqual(r["source"], "行为推断")
        self.assertTrue(r["canon"])
        self.assertIn("date", r)
        self.assertIn("id", r)

    def test_agent_record_requires_capability_event(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add_agent_record("第一次", capability_event="")
        with self.assertRaises(ValueError):
            self.store.add_agent_record("第一次", capability_event="   ")

    def test_canon_defaults_false(self) -> None:
        r = self.store.add_user_record("未册封")
        self.assertFalse(r["canon"])

    def test_shared_memory_roundtrip_and_luminance_clamp(self) -> None:
        self.store.add_shared_memory("陪你赶工", luminance=9)
        mems = self.store.shared_memories()
        self.assertEqual(len(mems), 1)
        self.assertEqual(mems[0]["event"], "陪你赶工")
        self.assertEqual(mems[0]["luminance"], 5)  # clamp 到 0-5

    def test_persist_and_reload(self) -> None:
        self.store.add_user_record("落盘测试", canon=True)
        reloaded = GrowthStore(root=self._root)
        self.assertEqual(len(reloaded.user_records()), 1)
        self.assertTrue(reloaded.user_records()[0]["canon"])

    def test_micro_cooling_roundtrip(self) -> None:
        self.assertEqual(self.store.micro_cooling(), {"cooldown_until": None, "last_type": None})
        self.store.set_micro_cooling("feedback", 123456.0)
        self.assertEqual(self.store.micro_cooling(), {"cooldown_until": 123456.0, "last_type": "feedback"})

    def test_micro_cooling_rejects_unknown_type(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set_micro_cooling("bogus", 0.0)

    def test_corrupt_file_resets_empty(self) -> None:
        (self._root / "growth.json").write_text("{ not valid json", encoding="utf-8")
        store = GrowthStore(root=self._root)
        self.assertEqual(store.user_records(), [])
        self.assertEqual(store.agent_records(), [])

    def test_records_sorted_desc_by_ts(self) -> None:
        self.store.add_user_record("早")
        self.store.add_user_record("晚")
        recs = self.store.user_records()
        self.assertGreaterEqual(recs[0]["ts"], recs[1]["ts"])

    def test_root_property_exposed(self) -> None:
        # 公开只读 root 属性（供业务层推导同目录状态文件，替代私有 _root 触碰）
        self.assertEqual(self.store.root, self._root)

    def test_reload_refreshes_from_disk(self) -> None:
        import json
        self.store.add_user_record("旧")
        path = self._root / "growth.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["user_track"].append({
            "id": "x", "ts": 0.0, "date": "2026-01-01",
            "milestone": "外部写入", "source": "explicit", "canon": False,
        })
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        # reload 前旧内存态不含"外部写入"
        self.assertFalse(any(r["milestone"] == "外部写入" for r in self.store.user_records()))
        # reload 后可见
        self.store.reload()
        self.assertTrue(any(r["milestone"] == "外部写入" for r in self.store.user_records()))


if __name__ == "__main__":
    unittest.main()
