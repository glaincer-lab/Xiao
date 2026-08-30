"""M3-M2 心跳引擎（backend/m3/heartbeat.py + content.py）单元测试。

TDD 契约：先写验收断言再实现满足。覆盖 docs/specs/M3-proactive.md：
    §4.2 心跳（定时→查记忆+日程→生成候选→内容质量门→消费预算→开口；无响应退避）
    §5 规则（主动类只建议不执行；回应率进 style_profile 仅记录）
    §7 验收断言（无响应 3 天降频断言）
    §8 开放问题 2（时段分布首版固定三档）

验收断言（5 条，见各 TestCase）：
    1. 无素材零投递：content 返回 None（或质量门不过）→ notifier.process() 不被调用
    2. 无响应 3 天降频：注入 now_fn 模拟连续 3 天无 note_user_response() → 自动降频；有人回应则不降
    3. 时段三档：早/中/晚 _should_run(now) 正确命中/不命中（首版固定三档）
    4. 候选生成走 process：命中时段 + 有素材 → notifier.process() 恰好一次
    5. 回应用户重置 streak：note_user_response() 后 no_response_streak == 0

运行：python -m unittest tests.test_m3_heartbeat -v
"""
from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta
from unittest import mock

from backend.m3.content import StubContent, build_candidate, quality_gate
from backend.m3.heartbeat import (
    DEFAULT_SLOTS,
    FREQUENCY_LADDER,
    HeartbeatEngine,
    slot_for,
)
from backend.m3.notify import DELIVERED


def _now(hour: int = 8, day: int = 30, month: int = 8, year: int = 2026, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute)


# 一张"有料"候选：四维分高、草案非空（能通过质量门与 score 阈值）
def _good_candidate() -> dict:
    return {
        "类型": "心跳",
        "内容草案": "早上好，今天早上九点你去拜访渠道商，路上记得带上最新报价单。",
        "特征": {"urgency": 0.8, "actionability": 0.9, "relationship": 0.5, "freshness": 0.7},
    }


def _make_engine(content, now_fn, notifier=None, **kw):
    if notifier is None:
        notifier = mock.Mock()
        notifier.process.return_value = DELIVERED
    return HeartbeatEngine(
        notifier=notifier,
        content=content,
        now_fn=now_fn,
        **kw,
    ), notifier


# =====================================================================
# 1. 无素材零投递断言（§4.2 内容质量门：无素材宁可不说）
# =====================================================================
class TestNoMaterialZeroDelivery(unittest.TestCase):
    def test_content_returns_none_skips_process(self) -> None:
        # 内容源返回 None（无素材）→ 不调用 notifier.process()、零投递
        notifier = mock.Mock()
        eng, _ = _make_engine(lambda now, ctx: None, lambda: _now(8), notifier)
        result = eng._run_once(_now(8))
        self.assertIsNone(result)
        notifier.process.assert_not_called()

    def test_low_quality_fails_gate_and_skips_process(self) -> None:
        # 内容源给了候选，但被质量门拦下（草案空 + 特征低）→ 零投递
        def weak_content(now, ctx):
            return {"类型": "心跳", "内容草案": "", "特征": {
                "urgency": 0.1, "actionability": 0.1, "relationship": 0.1, "freshness": 0.1,
            }}

        notifier = mock.Mock()
        eng, _ = _make_engine(weak_content, lambda: _now(8), notifier)
        result = eng._run_once(_now(8))
        self.assertIsNone(result)
        notifier.process.assert_not_called()

    def test_default_content_build_returns_none(self) -> None:
        # 默认内容源（首版不接 M1 检索）→ 返回 None / 零投递
        notifier = mock.Mock()
        eng, _ = _make_engine(build_candidate, lambda: _now(8), notifier)
        result = eng._run_once(_now(8))
        self.assertIsNone(result)
        notifier.process.assert_not_called()


# =====================================================================
# 2. 无响应 3 天降频断言（§4.2 / §7）
# =====================================================================
class TestDecayAfterNoResponse(unittest.TestCase):
    def _eng(self):
        now_ref = {"t": _now(8)}
        eng, _ = _make_engine(lambda now, ctx: _good_candidate(), lambda: now_ref["t"])
        return eng, now_ref

    def test_decay_after_3_days_no_response(self) -> None:
        eng, now_ref = self._eng()
        # 第 1 天投递（触发记录）
        self.assertEqual(eng._run_once(now_ref["t"]), DELIVERED)
        self.assertEqual(eng.no_response_streak, 0)
        self.assertFalse(eng._should_decay(now_ref["t"]))
        # 连续 3 天无响应（days 2/3/4 每天结算 +1）
        for exp in (1, 2, 3):
            now_ref["t"] = now_ref["t"] + timedelta(days=1)
            eng._should_decay(now_ref["t"])
            self.assertEqual(eng.no_response_streak, exp)
        now_ref["t"] = now_ref["t"] + timedelta(days=1)
        self.assertTrue(eng._should_decay(now_ref["t"]))
        # 触发自动降频（frequency 降档）
        old = eng.frequency
        self.assertTrue(eng._maybe_decay(now_ref["t"]))
        self.assertNotEqual(eng.frequency, old)
        self.assertEqual(eng.frequency, FREQUENCY_LADDER[FREQUENCY_LADDER.index(old) + 1])

    def test_no_decay_when_user_responds(self) -> None:
        eng, now_ref = self._eng()
        # 第 1 天投递
        eng._run_once(now_ref["t"])
        # 第 2 天推进后无响应 streak=1，随后用户回应 → 归零
        now_ref["t"] = now_ref["t"] + timedelta(days=1)
        eng._should_decay(now_ref["t"])
        self.assertEqual(eng.no_response_streak, 1)
        eng.note_user_response()
        self.assertEqual(eng.no_response_streak, 0)
        # 响应后再连续 2 天无响应 → streak=2，未到 3 → 不降频
        for _ in range(2):
            now_ref["t"] = now_ref["t"] + timedelta(days=1)
            eng._should_decay(now_ref["t"])
        self.assertEqual(eng.no_response_streak, 2)
        self.assertFalse(eng._should_decay(now_ref["t"]))
        self.assertEqual(eng.frequency, "daily")

    def test_decay_ladder_clamps_at_sparse(self) -> None:
        eng, now_ref = self._eng()
        eng.frequency = "sparse"  # 已最低
        # 即使应降频，也不再降
        self.assertIn(eng._apply_decay(), FREQUENCY_LADDER)  # 不抛错、保持档位
        self.assertEqual(eng.frequency, "sparse")


# =====================================================================
# 3. 时段三档断言（§4.2 / §8 开放问题 2：首版固定三档）
# =====================================================================
class TestSlotDetection(unittest.TestCase):
    def setUp(self) -> None:
        self.eng, _ = _make_engine(lambda now, ctx: None, lambda: _now(8))

    def test_default_slots_are_fixed_three(self) -> None:
        self.assertEqual(set(DEFAULT_SLOTS), {"early", "mid", "evening"})

    def test_early_slot(self) -> None:
        for h in (6, 7, 8, 9, 10, 11):
            self.assertTrue(self.eng._should_run(_now(h)), f"early hour {h} 应命中")
            self.assertEqual(slot_for(_now(h)), "early")

    def test_mid_slot(self) -> None:
        for h in (12, 13, 14, 15, 16, 17):
            self.assertTrue(self.eng._should_run(_now(h)), f"mid hour {h} 应命中")
            self.assertEqual(slot_for(_now(h)), "mid")

    def test_evening_slot(self) -> None:
        for h in (18, 19, 20, 21, 22, 23):
            self.assertTrue(self.eng._should_run(_now(h)), f"evening hour {h} 应命中")
            self.assertEqual(slot_for(_now(h)), "evening")

    def test_deep_night_no_slot(self) -> None:
        for h in (0, 1, 2, 3, 4, 5):
            self.assertFalse(self.eng._should_run(_now(h)), f"深夜 hour {h} 不应命中")
            self.assertIsNone(slot_for(_now(h)))

    def test_slot_boundaries(self) -> None:
        # 12 时整进入中档；18 时整进入晚档（半开区间 [start, end)）
        self.assertEqual(slot_for(_now(12)), "mid")
        self.assertEqual(slot_for(_now(18)), "evening")
        self.assertEqual(slot_for(_now(11)), "early")
        self.assertEqual(slot_for(_now(17)), "mid")
        self.assertEqual(slot_for(_now(23)), "evening")


# =====================================================================
# 4. 候选生成走 process 断言（§4.2 心跳链路）
# =====================================================================
class TestCandidateGoesThroughProcess(unittest.TestCase):
    def test_process_called_once_with_candidate(self) -> None:
        notifier = mock.Mock()
        notifier.process.return_value = DELIVERED
        captured = {}

        def content(now, ctx):
            captured["ctx"] = dict(ctx)
            return _good_candidate()

        eng, _ = _make_engine(content, lambda: _now(8), notifier)
        result = eng._run_once(_now(8))
        self.assertEqual(result, DELIVERED)
        notifier.process.assert_called_once()
        cand = notifier.process.call_args[0][0]
        self.assertEqual(cand["类型"], "心跳")
        self.assertEqual(cand["内容草案"], _good_candidate()["内容草案"])
        for k in ("urgency", "actionability", "relationship", "freshness"):
            self.assertIn(k, cand["特征"])
        # 上下文传给内容源：当前时段 + 频率
        self.assertEqual(captured["ctx"]["slot"], "early")
        self.assertEqual(captured["ctx"]["frequency"], "daily")

    def test_stub_injected_content_object(self) -> None:
        # 可注入 stub 内容源对象（含 build_candidate），不硬编码 M1 检索
        notifier = mock.Mock()
        notifier.process.return_value = DELIVERED
        stub = StubContent(preset=_good_candidate())
        eng, _ = _make_engine(stub, lambda: _now(8), notifier)
        self.assertEqual(eng._run_once(_now(8)), DELIVERED)
        notifier.process.assert_called_once()

    def test_off_slot_never_reaches_process(self) -> None:
        notifier = mock.Mock()
        eng, _ = _make_engine(lambda now, ctx: _good_candidate(), lambda: _now(8), notifier)
        self.assertIsNone(eng._run_once(_now(3)))  # 深夜非时段
        notifier.process.assert_not_called()


# =====================================================================
# 5. 回应用户重置 streak 断言（§4.2 无响应退避）
# =====================================================================
class TestUserResponseResetsStreak(unittest.TestCase):
    def test_note_user_response_resets_streak(self) -> None:
        now_ref = {"t": _now(8)}
        eng, _ = _make_engine(lambda now, ctx: _good_candidate(), lambda: now_ref["t"])
        # 制造无响应
        eng._run_once(now_ref["t"])
        now_ref["t"] = _now(8, day=31)
        eng._should_decay(now_ref["t"])
        self.assertEqual(eng.no_response_streak, 1)
        # 用户回应 → 归零
        eng.note_user_response()
        self.assertEqual(eng.no_response_streak, 0)

    def test_note_user_response_clears_after_accumulation(self) -> None:
        now_ref = {"t": _now(8)}
        eng, _ = _make_engine(lambda now, ctx: _good_candidate(), lambda: now_ref["t"])
        eng._run_once(now_ref["t"])
        for _ in range(2):
            now_ref["t"] = now_ref["t"] + timedelta(days=1)
            eng._should_decay(now_ref["t"])
        self.assertEqual(eng.no_response_streak, 2)
        eng.note_user_response()
        self.assertEqual(eng.no_response_streak, 0)
        # 且紧接着不应立即降频
        self.assertFalse(eng._maybe_decay(now_ref["t"]))


# =====================================================================
# 附加：质量门纯函数 / 定时循环启动停止（不真实 sleep，仅驱动周期性检查）
# =====================================================================
class TestQualityGateAndLifecycle(unittest.TestCase):
    def test_quality_gate_requires_material(self) -> None:
        self.assertFalse(quality_gate(None))
        self.assertFalse(quality_gate({}))
        self.assertFalse(quality_gate({"类型": "心跳", "内容草案": "", "特征": {"urgency": 1.0}}))
        self.assertFalse(quality_gate({"类型": "心跳", "内容草案": "有料", "特征": {"urgency": 0.1, "relationship": 0.1}}))
        self.assertTrue(quality_gate({"类型": "心跳", "内容草案": "有料", "特征": {"urgency": 0.9}}))
        self.assertTrue(quality_gate(_good_candidate()))

    def test_early_stage_stub_passthrough(self) -> None:
        # StubContent 可配置返回 None（模拟无素材）或返回候选
        stub = StubContent(preset=None)
        self.assertIsNone(stub.build_candidate(_now(8), {}))
        stub2 = StubContent(preset=_good_candidate())
        self.assertEqual(stub2.build_candidate(_now(8), {})["类型"], "心跳")

    def test_start_stop_runs_loop_without_sleeping_long(self) -> None:
        # 用极小 interval 驱动后台循环跑几拍，验证 start/stop 不抛错、能干净停；
        # content 返回 None → 零投递，无副作用。
        notifier = mock.Mock()
        eng, _ = _make_engine(lambda now, ctx: None, lambda: _now(8), notifier, interval_seconds=0.01)

        async def run():
            eng.start()
            self.assertTrue(eng._running)
            await asyncio.sleep(0.06)
            await eng.stop()

        asyncio.run(run())
        self.assertFalse(eng._running)
        self.assertIsNone(eng._task)


if __name__ == "__main__":
    unittest.main()
