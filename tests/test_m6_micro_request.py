"""M6 微小请求测试（M6-growth §4.2）：月频冷却、三类型话术、不表演断言、事件发布、可跳过。

验收断言（§7）：
1. 月频断言：同类 30 天内只问一次（冷却期内 maybe_ask 返回 None = 可跳过）。
2. 三类型断言：feedback / preference / human_experience 话术各自正确。
3. 不表演断言：human_experience 话术承认没有人类体验并请教，不含生理疲惫/感受表演词。
"""
from __future__ import annotations

import os
import shutil
import unittest
import uuid
from pathlib import Path

from backend.config import ROOT
from backend.event_bus import EventBus
from backend.m6.growth import MICRO_TYPES, GrowthStore
from backend.m6.micro_request import MicroRequester

DAY = 24 * 3600.0


def _make_tmp_dir() -> Path:
    """在 workspace 的 .tmp 下创建可写临时目录（沙箱拒写系统 Temp，见 test_memv4.py）。"""
    d = ROOT / ".tmp" / f"m6_micro_{uuid.uuid4().hex[:8]}"
    os.makedirs(d, exist_ok=True)
    return d


class Clock:
    """可推进假时钟（注入 now_fn，隔离真实时间）。"""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class MicroRequesterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_tmp_dir()
        self.store = GrowthStore(root=self._tmp)
        self.bus = EventBus()
        self.clock = Clock()
        self.req = MicroRequester(self.store, self.bus, now_fn=self.clock, cooldown_days=30)
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))

    # ---------- 1. 月频断言：同类 30 天内只问一次 ----------

    def test_same_type_asked_once_within_month(self) -> None:
        self.assertIsNotNone(self.req.maybe_ask("feedback"))
        self.req.record_asked("feedback", "可以啊")
        self.clock.advance(10 * DAY)  # 10 天后仍在冷却
        self.assertIsNone(self.req.maybe_ask("feedback"))
        self.clock.advance(25 * DAY)  # 累计 35 天，已过月
        self.assertIsNotNone(self.req.maybe_ask("feedback"))

    def test_cooling_persisted_in_store(self) -> None:
        self.req.record_asked("preference", "是认真的")
        mc = self.store.micro_cooling()
        self.assertEqual(mc["last_type"], "preference")
        self.assertGreater(mc["cooldown_until"], self.clock.now)

    def test_cooling_survives_new_instance(self) -> None:
        """跨重启：新 MicroRequester 实例（无进程内状态）仍被 store 冷却挡住同类。"""
        self.req.record_asked("human_experience", "说不清，就是晕乎乎的")
        fresh = MicroRequester(self.store, self.bus, now_fn=self.clock, cooldown_days=30)
        self.assertIsNone(fresh.maybe_ask("human_experience"))

    def test_different_type_not_blocked_by_same_type_cooling(self) -> None:
        """冷却只挡同类；不同类不受影响（轮内只问一类由编排层保证）。"""
        self.req.record_asked("feedback", "可以")
        self.assertIsNone(self.req.maybe_ask("feedback"))
        self.assertIsNotNone(self.req.maybe_ask("preference"))
        self.assertIsNotNone(self.req.maybe_ask("human_experience"))

    def test_custom_cooldown_days(self) -> None:
        req = MicroRequester(self.store, self.bus, now_fn=self.clock, cooldown_days=1)
        self.assertIsNotNone(req.maybe_ask("feedback"))
        req.record_asked("feedback")
        self.clock.advance(DAY - 1)  # 未满 1 天：冷却中
        self.assertIsNone(req.maybe_ask("feedback"))
        self.clock.advance(2)  # 满 1 天：可再问
        self.assertIsNotNone(req.maybe_ask("feedback"))

    # ---------- 2. 三类型断言：三类话术各自正确 ----------

    def test_three_types_scripts(self) -> None:
        fb = self.req.maybe_ask("feedback")
        pf = self.req.maybe_ask("preference")
        hx = self.req.maybe_ask("human_experience")
        # feedback：求反馈，可存进成长记录
        self.assertIn("干得漂亮", fb)
        self.assertIn("成长记录", fb)
        # preference：低置信条目顺带确认
        self.assertIn("拿不准", pf)
        # human_experience：数字无能式提问，承认没有人类体验并请教
        self.assertIn("微醺", hx)
        self.assertIn("我懂字面意思", hx)
        self.assertIn("请教", hx)

    def test_maybe_ask_defaults_to_feedback(self) -> None:
        """无参调用默认求反馈（开箱即用）。"""
        self.assertIn("干得漂亮", self.req.maybe_ask())

    # ---------- 3. 不表演断言：human_experience 不含生理疲惫/感受表演 ----------

    FORBIDDEN_FEELING_WORDS = [
        "我累", "我困", "我饿", "我疲惫", "我感受到", "我感觉",
        "我渴望", "我好想", "我期待", "我兴奋", "我开心", "我难过",
    ]

    def test_human_experience_no_performance(self) -> None:
        hx = self.req.maybe_ask("human_experience")
        for word in self.FORBIDDEN_FEELING_WORDS:
            self.assertNotIn(word, hx, f"human_experience 话术含感受表演词：{word}")

    # ---------- 事件与健壮性 ----------

    def test_record_asked_emits_micro_request_asked(self) -> None:
        seen: list[dict] = []
        self.bus.on("micro_request.asked", seen.append)
        self.req.record_asked("human_experience", "第一次喝到桂花酒的时候")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["类型"], "human_experience")
        self.assertEqual(seen[0]["用户响应"], "第一次喝到桂花酒的时候")

    def test_record_asked_allows_null_user_response(self) -> None:
        seen: list[dict] = []
        self.bus.on("micro_request.asked", seen.append)
        self.req.record_asked("feedback")
        self.assertEqual(seen[0]["类型"], "feedback")
        self.assertIsNone(seen[0]["用户响应"])

    def test_unknown_kind_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.req.maybe_ask("bogus")
        with self.assertRaises(ValueError):
            self.req.record_asked("bogus")

    def test_all_types_covered_by_micro_types(self) -> None:
        for kind in ("feedback", "preference", "human_experience"):
            self.assertIn(kind, MICRO_TYPES)


if __name__ == "__main__":
    unittest.main()
