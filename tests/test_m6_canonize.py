"""M6 §4.1 双源入册制（backend/m6/canonize.py）单元测试。

覆盖验收断言：
1. 无 canon=true 系统自入册：不经用户册封，落库 canon 必 False（候选识别/轻提阶段不写库）；
2. 同类 90 天冷却：拒绝后同类候选 90 天内不再轻提（冷却键=事件类型）；
3. 册封询问周 ≤1 次（每候选分项上限）；
4. Aging Policy：连续 3 周被顺延，第 4 周提权强制输出；
5. 候选识别 schema：task.completed + vision.feedback(接受) 双源齐备才生成候选。

仅标准库；本文件 MIT。
"""
from __future__ import annotations

import os
import shutil
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.config import ROOT
from backend.event_bus import EventBus
from backend.m6.growth import GrowthStore
from backend.m6.canonize import Canonizer, PROMPT_TEXT


def _make_tmp_dir() -> Path:
    """在 workspace 的 .tmp 下创建可写临时目录（沙箱拒写系统 Temp，见 test_memv4.py）。"""
    d = ROOT / ".tmp" / f"m6_canonize_{uuid.uuid4().hex[:8]}"
    os.makedirs(d, exist_ok=True)
    return d


class _Clock:
    """可控时钟：now_fn 注入 + advance 推进（模拟周/天/秒流逝）。"""

    def __init__(self, start: float) -> None:
        self._ts = start

    def now(self) -> float:
        return self._ts

    def advance(self, seconds: float) -> None:
        self._ts += seconds

    def advance_days(self, days: float) -> None:
        self._ts += days * 86400.0


# 基准时刻：2026-08-31 00:00 UTC（一个周一）
BASE_TS = datetime(2026, 8, 31, tzinfo=timezone.utc).timestamp()


def _task_completed(task_id: str = "t1", **result) -> dict:
    payload = {"task_id": task_id, "result": result, "node_count": 2, "failed_count": 0}
    return payload


def _feedback(v: str) -> dict:
    return {"三态": v}


class CanonizerBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_tmp_dir()
        self._root = self._tmp
        self.bus = EventBus()
        self.store = GrowthStore(root=self._root)
        self.clock = _Clock(BASE_TS)
        self.recv_candidate: list[dict] = []
        self.recv_canonized: list[dict] = []
        self._unsubs = []
        self._unsubs.append(self.bus.on("growth.candidate", self.recv_candidate.append))
        self._unsubs.append(self.bus.on("growth.canonized", self.recv_canonized.append))
        self.c = self._make_canonizer()
        self.c.start()
        self.addCleanup(self._cleanup)

    def _make_canonizer(self, **kw) -> Canonizer:
        return Canonizer(bus=self.bus, store=self.store, now_fn=self.clock.now, **kw)

    def _cleanup(self) -> None:
        for u in self._unsubs:
            u()
        self.c.close()
        shutil.rmtree(self._tmp, ignore_errors=True)


class CandidateRecognitionTests(CanonizerBase):
    """验收 5：候选识别 schema——双源齐备才生成候选。"""

    def test_task_completed_alone_no_candidate(self) -> None:
        self.bus.emit("task.completed", _task_completed())
        self.assertEqual(self.c.pending_candidates(), [])
        self.assertEqual(self.recv_candidate, [])

    def test_positive_feedback_alone_no_candidate(self) -> None:
        self.bus.emit("vision.feedback", _feedback("接受"))
        self.assertEqual(self.c.pending_candidates(), [])
        self.assertEqual(self.recv_candidate, [])

    def test_both_sources_generate_candidate(self) -> None:
        self.bus.emit("task.completed", _task_completed(task_id="t1", summary="帮老板把周报发出去"))
        self.bus.emit("vision.feedback", _feedback("接受"))
        cands = self.c.pending_candidates()
        self.assertEqual(len(cands), 1)
        self.assertIn("帮老板把周报发出去", cands[0]["milestone"])
        # growth.candidate 事件已广播 {事件,能力凭证}
        self.assertEqual(len(self.recv_candidate), 1)
        self.assertIn("事件", self.recv_candidate[0])
        self.assertIn("能力凭证", self.recv_candidate[0])

    def test_negative_or_partial_feedback_no_candidate(self) -> None:
        self.bus.emit("task.completed", _task_completed())
        self.bus.emit("vision.feedback", _feedback("不接受"))
        self.assertEqual(self.c.pending_candidates(), [])
        self.assertEqual(self.recv_candidate, [])
        self.bus.emit("task.completed", _task_completed(task_id="t2"))
        self.bus.emit("vision.feedback", _feedback("部分"))
        self.assertEqual(self.c.pending_candidates(), [])
        self.assertEqual(self.recv_candidate, [])

    def test_candidate_expires_after_match_window(self) -> None:
        self.bus.emit("task.completed", _task_completed(task_id="t1"))
        self.clock.advance(601)  # 超过默认 600s 配对窗口
        self.bus.emit("vision.feedback", _feedback("接受"))
        self.assertEqual(self.c.pending_candidates(), [])


class NoSelfCanonTests(CanonizerBase):
    """验收 1：无 canon=true 系统自入册——不经用户册封，落库 canon 必 False。"""

    def test_identification_and_prompt_do_not_write_store(self) -> None:
        self.bus.emit("task.completed", _task_completed(task_id="t1", summary="完成任务"))
        self.bus.emit("vision.feedback", _feedback("接受"))
        self.assertIsNotNone(self.c.poll_ask())
        self.assertEqual(self.store.user_records(), [])
        self.assertEqual(self.store.agent_records(), [])
        # 库内 canon=True 记录为 0
        canon_total = sum(1 for r in self.store.user_records() + self.store.agent_records() if r["canon"])
        self.assertEqual(canon_total, 0)

    def test_canon_true_only_after_user_confirmation(self) -> None:
        self.bus.emit("task.completed", _task_completed(task_id="t1", summary="搞定方案"))
        self.bus.emit("vision.feedback", _feedback("接受"))
        self.c.poll_ask()
        result = self.c.handle_user_reply("好")
        self.assertTrue(result["ok"])
        records = self.store.user_records() + self.store.agent_records()
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["canon"])


class CooldownTests(CanonizerBase):
    """验收 2：同类 90 天冷却（冷却键=事件类型）。"""

    def _identify_capability_candidate(self, task_id: str = "t1") -> None:
        self.bus.emit(
            "task.completed",
            _task_completed(
                task_id=task_id,
                summary="第一次自动调温",
                capability_event="ha_scene_auto",
                event_type="ha_scene_auto",
            ),
        )
        self.bus.emit("vision.feedback", _feedback("接受"))

    def test_reject_sets_cooldown_and_blocks_same_type(self) -> None:
        self._identify_capability_candidate()
        cand = self.c.pending_candidates()[0]
        result = self.c.reject(cand["id"])
        self.assertTrue(result["ok"])
        # 冷却键 = 事件类型
        cd = self.c.cooldowns()
        self.assertIn("ha_scene_auto", cd)
        until = cd["ha_scene_auto"]
        self.assertAlmostEqual(until - BASE_TS, 90 * 86400, delta=5)
        # 冷却期内同类候选不再生成（也不再轻提）
        self._identify_capability_candidate(task_id="t2")
        self.assertEqual(self.c.pending_candidates(), [])
        self.assertIsNone(self.c.poll_ask())

    def test_cooldown_expires_after_90_days(self) -> None:
        self._identify_capability_candidate()
        self.c.reject(self.c.pending_candidates()[0]["id"])
        self.clock.advance_days(91)
        self._identify_capability_candidate(task_id="t2")
        cands = self.c.pending_candidates()
        self.assertEqual(len(cands), 1)
        self.assertIsNotNone(self.c.poll_ask())

    def test_cooldown_persists_across_restart(self) -> None:
        self._identify_capability_candidate()
        self.c.reject(self.c.pending_candidates()[0]["id"])
        # 同 root 重建 Canonizer：冷却仍在（重启不失忆）
        c2 = self._make_canonizer()
        c2.start()
        try:
            self.assertIn("ha_scene_auto", c2.cooldowns())
            self.bus.emit(
                "task.completed",
                _task_completed(task_id="t2", summary="同类", capability_event="ha_scene_auto", event_type="ha_scene_auto"),
            )
            self.bus.emit("vision.feedback", _feedback("接受"))
            self.assertEqual(c2.pending_candidates(), [])
            self.assertIsNone(c2.poll_ask())
        finally:
            c2.close()


class WeeklyAskLimitTests(CanonizerBase):
    """验收 3：册封询问周 ≤1 次（每候选分项上限）。"""

    def test_ask_at_most_once_per_week(self) -> None:
        self.bus.emit("task.completed", _task_completed(task_id="t1", summary="完成任务"))
        self.bus.emit("vision.feedback", _feedback("接受"))
        first = self.c.poll_ask()
        self.assertEqual(first, PROMPT_TEXT)
        # 同周再次询问 → 不再轻提
        self.assertIsNone(self.c.poll_ask())
        # 下周可再次轻提同一候选
        self.clock.advance_days(7)
        self.assertEqual(self.c.poll_ask(), PROMPT_TEXT)


class AgingPolicyTests(CanonizerBase):
    """验收 4：连续 3 周被顺延，第 4 周提权强制输出。"""

    def _make_canonizer(self, **kw) -> Canonizer:
        # 全局预算恒满：模拟 M1 澄清/M2 印证长期占用（M6 册封优先级最低）
        kw.setdefault("budget_hook", lambda: 5)
        return super()._make_canonizer(**kw)

    def test_boost_after_three_consecutive_deferred_weeks(self) -> None:
        self.bus.emit("task.completed", _task_completed(task_id="t1", summary="完成任务"))
        self.bus.emit("vision.feedback", _feedback("接受"))
        cand_id = self.c.pending_candidates()[0]["id"]

        # 第 1~3 周：预算被占满，顺延（不输出）
        for week in (1, 2, 3):
            if week > 1:
                self.clock.advance_days(7)
            self.assertIsNone(self.c.poll_ask(), f"第 {week} 周应顺延")
        cand = self.c._pending_candidate(cand_id)
        self.assertEqual(cand["deferred_weeks"], 3)

        # 第 4 周：预算仍满，但 Aging 提权 → 无条件输出
        self.clock.advance_days(7)
        self.assertEqual(self.c.poll_ask(), PROMPT_TEXT)


class TrackAndEmitTests(CanonizerBase):
    """轨别判定 + 入册事件广播。"""

    def test_capability_candidate_lands_on_agent_track(self) -> None:
        self.bus.emit(
            "task.completed",
            _task_completed(task_id="t1", summary="第一次自动调温", capability_event="ha_scene_auto"),
        )
        self.bus.emit("vision.feedback", _feedback("接受"))
        cand = self.c.pending_candidates()[0]
        self.assertEqual(cand["kind"], "agent")
        result = self.c.handle_user_reply("记上")
        self.assertTrue(result["ok"])
        self.assertEqual(result["track"], "agent")
        records = self.store.agent_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["capability_event"], "ha_scene_auto")
        self.assertTrue(records[0]["canon"])
        # growth.canonized 广播 {记录id, 轨别}
        self.assertEqual(len(self.recv_canonized), 1)
        self.assertEqual(self.recv_canonized[0]["记录id"], result["record_id"])
        self.assertEqual(self.recv_canonized[0]["轨别"], "agent")

    def test_user_candidate_lands_on_user_track_with_auto_source(self) -> None:
        self.bus.emit("task.completed", _task_completed(task_id="t1", summary="拿下项目", event_type="work"))
        self.bus.emit("vision.feedback", _feedback("接受"))
        cand = self.c.pending_candidates()[0]
        self.assertEqual(cand["kind"], "user")
        result = self.c.handle_user_reply("好")
        self.assertTrue(result["ok"])
        self.assertEqual(result["track"], "user")
        records = self.store.user_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["milestone"], "拿下项目")
        self.assertEqual(records[0]["source"], "自动识别")
        self.assertTrue(records[0]["canon"])
        self.assertEqual(self.recv_canonized[0]["轨别"], "user")

    def test_unrecognized_reply_no_action(self) -> None:
        self.bus.emit("task.completed", _task_completed(task_id="t1", summary="完成任务"))
        self.bus.emit("vision.feedback", _feedback("接受"))
        self.c.poll_ask()
        result = self.c.handle_user_reply("随便吧")
        self.assertFalse(result["ok"])
        # 未识别回复：不写库、不冷却、候选仍待处理
        self.assertEqual(self.store.user_records() + self.store.agent_records(), [])
        self.assertEqual(self.c.cooldowns(), {})
        self.assertEqual(len(self.c.pending_candidates()), 1)


if __name__ == "__main__":
    unittest.main()
