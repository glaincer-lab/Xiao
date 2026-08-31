"""M3-M4 DORMANT 订阅协调器（backend/m3/dormant.py）单元测试。

TDD 契约：先写验收断言再实现，覆盖 docs/specs/M3-proactive.md：
    §4.4 DORMANT 联动（M0 硬约束）—— 本引擎冻结非降频，零消息零归因；
          托付任务照常办不推送，进展进 RETURNING 简报。
    §6  事件契约—— 订阅 macro.state_changed（已登记，只订阅不新增）。
红线：M3-M4 不重做 DORMANT 状态机、不修 backend/macro_state.py；
      只订阅 + 只读宏态快照（is_proactive_allowed / regression_brief 等）。

验收断言（4 条核心，见各 TestCase）：
    1. DORMANT 冻结零消息断言：macro.state_changed 后态 DORMANT → frozen；
       RETURNING/ACTIVE → 解冻（不生成候选/不投递）。
    2. 衔接 M3-M2 heartbeat / M3-M3 event_trigger：冻结时生成候选前早退（零投递）。
    3. 订阅事件登记一致断言：订阅的 macro.state_changed 已在 event_bus.EVENT_TYPES 白名单。
    4. 只读宏态断言：coordinator 只读 is_proactive_allowed/regression_brief 等，
       绝不调用 macro_state 的写方法（tick/on_user_dialogue/save/on_background_completion）。

运行：python -m unittest tests.test_m3_dormant -v
"""
from __future__ import annotations

import unittest
from unittest import mock

from backend.event_bus import EVENT_TYPES, EventBus
from backend.m3.dormant import SUBSCRIBED_EVENTS, DormantCoordinator
from backend.m3.event_trigger import EventTriggerEngine, ROUTE_FROZEN
from backend.m3.heartbeat import HeartbeatEngine
from backend.m3.notify import DELIVERED


def _dormant_payload(old: str, new: str) -> dict:
    return {"前态": old, "后态": new, "时长": 0.0}


class _StubContent:
    """内容源 stub：始终返回一个候选（供 heartbeat 测试可进 process 前被冻结检查早退）。"""

    def __init__(self, preset: dict | None = None) -> None:
        self.preset = preset or {
            "类型": "心跳",
            "内容草案": "测试心跳草稿",
            "特征": {"urgency": 0.7, "actionability": 0.6, "relationship": 0.5, "freshness": 0.6},
        }

    def build_candidate(self, now, ctx):
        return self.preset


# =====================================================================
# 1. DORMANT 冻结 / 解冻断言（§4.4）：订阅 macro.state_changed
# =====================================================================
class TestDormantFreeze(unittest.TestCase):
    def test_dormant_event_freezes(self) -> None:
        bus = EventBus()
        coord = DormantCoordinator(bus=bus)
        bus.emit("macro.state_changed", _dormant_payload("IDLE", "DORMANT"))
        self.assertTrue(coord.is_frozen())
        self.assertTrue(coord.frozen)

    def test_returning_event_thaws(self) -> None:
        bus = EventBus()
        coord = DormantCoordinator(bus=bus)
        bus.emit("macro.state_changed", _dormant_payload("IDLE", "DORMANT"))
        self.assertTrue(coord.is_frozen())
        bus.emit("macro.state_changed", _dormant_payload("DORMANT", "RETURNING"))
        self.assertFalse(coord.is_frozen())

    def test_active_event_thaws(self) -> None:
        bus = EventBus()
        coord = DormantCoordinator(bus=bus)
        bus.emit("macro.state_changed", _dormant_payload("IDLE", "DORMANT"))
        bus.emit("macro.state_changed", _dormant_payload("DORMANT", "ACTIVE"))
        self.assertFalse(coord.is_frozen())

    def test_idle_event_keeps_thawed(self) -> None:
        # IDLE 非 DORMANT，M0 语义 is_proactive_allowed()==True → 不冻结
        bus = EventBus()
        coord = DormantCoordinator(bus=bus)
        bus.emit("macro.state_changed", _dormant_payload("ACTIVE", "IDLE"))
        self.assertFalse(coord.is_frozen())

    def test_initial_snapshot_from_macro_if_already_dormant(self) -> None:
        # 构造时宏态已是 DORMANT（is_proactive_allowed False）→ 初始即冻结
        bus = EventBus()
        macro = mock.Mock()
        macro.is_proactive_allowed.return_value = False  # DORMANT
        coord = DormantCoordinator(bus=bus, macro=macro)
        self.assertTrue(coord.is_frozen())
        macro.is_proactive_allowed.assert_called()

    def test_initial_snapshot_thawed_when_active(self) -> None:
        bus = EventBus()
        macro = mock.Mock()
        macro.is_proactive_allowed.return_value = True  # ACTIVE
        coord = DormantCoordinator(bus=bus, macro=macro)
        self.assertFalse(coord.is_frozen())


# =====================================================================
# 2. 衔接 M3-M2 heartbeat / M3-M3 event_trigger：冻结时生成候选前早退（§4.4）
# =====================================================================
class TestFrozenGateIntegration(unittest.TestCase):
    def test_heartbeat_skips_candidate_when_frozen(self) -> None:
        frozen = mock.Mock()
        frozen.is_frozen.return_value = True
        notifier = mock.Mock()
        notifier.process.return_value = DELIVERED
        hb = HeartbeatEngine(
            notifier=notifier,
            content=_StubContent(),
            dormant=frozen,
            now_fn=lambda: __import__("datetime").datetime(2026, 9, 10, 10),
        )
        status = hb._run_once(hb._now_fn())
        self.assertIsNone(status)          # 冻结早退，零投递
        notifier.process.assert_not_called()  # 不生成候选、不投递

    def test_heartbeat_delivers_when_thawed(self) -> None:
        thawed = mock.Mock()
        thawed.is_frozen.return_value = False
        notifier = mock.Mock()
        notifier.process.return_value = DELIVERED
        hb = HeartbeatEngine(
            notifier=notifier,
            content=_StubContent(),
            dormant=thawed,
            now_fn=lambda: __import__("datetime").datetime(2026, 9, 10, 10),
        )
        status = hb._run_once(hb._now_fn())
        self.assertEqual(status, DELIVERED)
        notifier.process.assert_called_once()

    def test_event_trigger_skips_candidate_when_frozen(self) -> None:
        frozen = mock.Mock()
        frozen.is_frozen.return_value = True
        notifier = mock.Mock()
        notifier.process.return_value = DELIVERED
        eng = EventTriggerEngine(bus=EventBus(), notifier=notifier, dormant=frozen)
        status = eng._route_candidate({
            "类型": "环境异常",
            "内容草案": "检测到温度异常。",
            "特征": {"urgency": 0.7, "actionability": 0.6, "relationship": 0.2, "freshness": 0.8},
            "_agg_key": "env.temperature",
            "_event_type": "env.anomaly",
        })
        self.assertEqual(status, ROUTE_FROZEN)
        notifier.process.assert_not_called()

    def test_event_trigger_delivers_when_thawed(self) -> None:
        thawed = mock.Mock()
        thawed.is_frozen.return_value = False
        notifier = mock.Mock()
        notifier.process.return_value = DELIVERED
        eng = EventTriggerEngine(bus=EventBus(), notifier=notifier, dormant=thawed)
        status = eng._route_candidate({
            "类型": "环境异常",
            "内容草案": "检测到温度异常。",
            "特征": {"urgency": 0.9, "actionability": 0.8, "relationship": 0.4, "freshness": 0.8},
            "_agg_key": "env.temperature",
            "_event_type": "env.anomaly",
        })
        self.assertEqual(status, DELIVERED)
        notifier.process.assert_called_once()


# =====================================================================
# 3. 订阅事件登记一致断言（§6：只订阅已登记事件，不新增/不改白名单）
# =====================================================================
class TestSubscribedEventsRegistered(unittest.TestCase):
    def test_subscribed_macro_state_changed_in_whitelist(self) -> None:
        self.assertEqual(SUBSCRIBED_EVENTS, ("macro.state_changed",))
        for evt in SUBSCRIBED_EVENTS:
            self.assertIn(evt, EVENT_TYPES)

    def test_coordinator_subscribes_macro_state_changed(self) -> None:
        bus = EventBus()
        coord = DormantCoordinator(bus=bus)
        self.assertEqual(bus.count("macro.state_changed"), 1)
        coord.close()
        self.assertEqual(bus.count("macro.state_changed"), 0)


# =====================================================================
# 4. 只读宏态断言：coordinator 不修改 macro_state（不调写方法、不新增依赖）
# =====================================================================
class TestReadOnlyMacroSnapshot(unittest.TestCase):
    def test_only_reads_macro_state(self) -> None:
        bus = EventBus()
        macro = mock.Mock()  # 普通 mock：未调用的写方法断言通过
        macro.is_proactive_allowed.return_value = True
        macro.is_dormant.return_value = False
        macro.regression_brief.return_value = "回归简报：任务 A 完成。"
        coord = DormantCoordinator(bus=bus, macro=macro)
        # 只读查询
        self.assertTrue(coord.proactive_allowed())
        self.assertEqual(coord.regression_brief(), "回归简报：任务 A 完成。")
        self.assertFalse(coord.is_dormant())
        # 写方法绝未被调用
        for name in ("tick", "on_user_dialogue", "on_non_dialogue_interaction",
                     "on_background_completion", "save", "load"):
            getattr(macro, name).assert_not_called()

    def test_lazy_default_macro_is_module_singleton(self) -> None:
        # 缺省不传 macro → 懒加载 backend.macro_state.macro_state 单例（只读）
        from backend.macro_state import macro_state
        bus = EventBus()
        coord = DormantCoordinator(bus=bus)
        self.assertIs(coord._get_macro(), macro_state)


if __name__ == "__main__":
    unittest.main()
