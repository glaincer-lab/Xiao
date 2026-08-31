"""M6-M1 回顾推送接入测试：三源分栏、双轨分离、被动问随时可答、共同回忆"咱们"叙事。"""
from __future__ import annotations

import os
import shutil
import time
import unittest
import uuid
from pathlib import Path

from backend.config import ROOT
from backend.m6.growth import GrowthStore
from backend.m6.recall import RecallComposer


def _make_tmp_dir() -> Path:
    """在 workspace 的 .tmp 下创建可写临时目录（沙箱拒写系统 Temp，见 test_memv4.py）。"""
    d = ROOT / ".tmp" / f"m6_recall_test_{uuid.uuid4().hex[:8]}"
    os.makedirs(d, exist_ok=True)
    return d


class RecallComposerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_tmp_dir()
        self.store = GrowthStore(root=self._tmp)
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        # 素材：双轨 + 共同记忆（时间倒序依据）
        # 注：本机 Windows 时钟 tick ~15.6ms，紧邻调用 time.time() 相同，
        # 故"早/晚"之间 sleep 0.02 跨 tick，保证 ts 可区分、store 倒序稳定。
        self.store.add_user_record("早的用户事", date="2026-01-01")
        time.sleep(0.02)
        self.store.add_user_record("晚的用户事", date="2026-06-01")
        self.store.add_agent_record("早的成长", capability_event="ha_scene_auto", date="2026-02-01")
        time.sleep(0.02)
        self.store.add_agent_record("晚的成长", capability_event="memory_export_done", date="2026-05-01")
        self.store.add_shared_memory("早的共同回忆", luminance=3, date="2026-03-01")
        time.sleep(0.02)
        self.store.add_shared_memory("晚的共同回忆", luminance=5, date="2026-04-01")
        self.composer = RecallComposer(self.store)

    # ---- compose：三源分栏 ----

    def test_compose_returns_three_tracks(self) -> None:
        data = self.composer.compose()
        self.assertEqual(set(data), {"user_track", "agent_track", "shared"})
        self.assertEqual(len(data["user_track"]), 2)
        self.assertEqual(len(data["agent_track"]), 2)
        self.assertEqual(len(data["shared"]), 2)

    def test_compose_tracks_keep_separate_fields(self) -> None:
        """验收 §7-1：双轨分离——各轨不混入对方字段。"""
        data = self.composer.compose()
        for r in data["user_track"]:
            self.assertNotIn("capability_event", r)
            self.assertIn("source", r)
            self.assertIn("milestone", r)
        for r in data["agent_track"]:
            self.assertNotIn("source", r)
            self.assertIn("capability_event", r)
            self.assertIn("milestone", r)
        for r in data["shared"]:
            self.assertIn("event", r)
            self.assertIn("luminance", r)

    def test_compose_each_track_time_desc(self) -> None:
        data = self.composer.compose()
        self.assertEqual(data["user_track"][0]["milestone"], "晚的用户事")
        self.assertEqual(data["user_track"][1]["milestone"], "早的用户事")
        self.assertEqual(data["agent_track"][0]["milestone"], "晚的成长")
        self.assertEqual(data["shared"][0]["event"], "晚的共同回忆")
        self.assertEqual(data["shared"][1]["event"], "早的共同回忆")

    def test_compose_is_readonly_snapshot(self) -> None:
        """铁规 3：只读快照——篡改 compose 结果不影响 store。"""
        data = self.composer.compose()
        data["user_track"][0]["milestone"] = "被篡改"
        self.assertEqual(self.store.user_records()[0]["milestone"], "晚的用户事")

    # ---- passive_answer：被动问随时可答 ----

    def test_passive_answer_nonempty_with_data(self) -> None:
        """验收 §7-2：被动问无论素材多少都返回非空内容。"""
        answer = self.composer.passive_answer()
        self.assertIsInstance(answer, str)
        self.assertTrue(answer.strip())

    def test_passive_answer_nonempty_with_empty_store(self) -> None:
        empty = RecallComposer(GrowthStore(root=_make_tmp_dir()))
        answer = empty.passive_answer()
        self.assertIsInstance(answer, str)
        self.assertTrue(answer.strip())

    def test_passive_answer_shared_uses_zanmen(self) -> None:
        """验收 §7-3：共同回忆叙事用"咱们"（叙事宪章）。"""
        answer = self.composer.passive_answer()
        self.assertIn("咱们", answer)

    def test_passive_answer_carries_track_facts(self) -> None:
        """素材事实（浪漫化转述）出现在文案里，双轨 + 共同记忆全覆盖。"""
        answer = self.composer.passive_answer()
        self.assertIn("晚的用户事", answer)
        self.assertIn("晚的成长", answer)
        self.assertIn("晚的共同回忆", answer)

    def test_passive_answer_shared_desc_order(self) -> None:
        """共同回忆时间倒序：晚的回忆先于早的回忆出现。"""
        answer = self.composer.passive_answer()
        self.assertLess(answer.index("晚的共同回忆"), answer.index("早的共同回忆"))

    def test_now_fn_injectable(self) -> None:
        """构造器接受 now_fn 注入（推送调度预留），不影响被动问。"""
        comp = RecallComposer(self.store, now_fn=lambda: 1234567890.0)
        self.assertTrue(comp.passive_answer().strip())

    def test_composer_rejects_non_growth_store(self) -> None:
        with self.assertRaises(TypeError):
            RecallComposer(None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
