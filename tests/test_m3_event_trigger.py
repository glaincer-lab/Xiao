"""M3-M3 事件触发引擎（backend/m3/event_trigger.py）单元测试。

TDD 契约：先写验收断言再实现，覆盖 docs/specs/M3-proactive.md：
    §4.3 事件触发 —— 事件源→相关性→紧急度→通知策略→同类窗口聚合→冷却
    §3  grief_schedule 哀伤节奏（3/7/30 天，M1.6 喂入）；calendar_anniversaries 纪念日
    §6  事件契约（订阅已有事件，不新增/不改白名单）
    §7  验收断言（哀伤节奏 3/7/30 天触发；紧急穿透仅限用户配置清单；同类窗口聚合防风暴）
    §8  开放问题 3（首版只上线天气+日程两源预判；历史偏好 join 可 stub/省略）

验收断言（6 条核心，见各 TestCase）：
    1. 哀伤节奏 3/7/30 天触发断言：grief_schedule 节点到点（3/7/30）→ 触发候选；未到点不触发
    2. 紧急穿透仅限用户配置清单断言：命中 emergency_passthrough → 穿透；未命中 → 不穿透（走常规）
    3. 同类窗口聚合（防风暴）断言：30 分钟窗口内多个同一类事件 → 只触发一次（aggregate.py 断言）
    4. 事件到达 → 相关性/紧急度 → 生成候选 → 调 notifier.process() 断言（mock notifier）
    5. 预判型（天气+日程两源）：天气降温 + 明日日程 → 生成预判候选（stub weather/schedule）
    6. 订阅事件登记一致断言：订阅的 6 个事件名均在 event_bus.EVENT_TYPES 白名单

运行：python -m unittest tests.test_m3_event_trigger -v
"""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest import mock

from backend.event_bus import EVENT_TYPES, EventBus
from backend.m3.notify import DELIVERED
from backend.m3.event_trigger import SUBSCRIBED_EVENTS, EventTriggerEngine


def _now(hour: int = 10, day: int = 10, month: int = 9, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour)


# ---- 可注入 stub（§8 首版只接天气+日程两源；历史偏好 join 可省略） ----

class StubWeather:
    """天气源 stub：today_temp_c / tomorrow_min_c 可配置（默认不降温→不触发预判）。"""

    def __init__(self, today: float | None = 20.0, tomorrow_min: float | None = 18.0) -> None:
        self.today = today
        self.tomorrow_min = tomorrow_min

    def today_temp_c(self) -> float | None:
        return self.today

    def tomorrow_min_c(self) -> float | None:
        return self.tomorrow_min


class StubSchedule:
    """日程源 stub：tomorrow_agenda 可配置（默认无明日日程→不触发预判）。"""

    def __init__(self, agenda: list | None = None) -> None:
        self.agenda = agenda if agenda is not None else []

    def tomorrow_agenda(self, now: datetime) -> list:
        return list(self.agenda)


def _make_engine(bus, notifier, config=None, now_fn=None, weather=None, schedule=None):
    if notifier is None:
        notifier = mock.Mock()
        notifier.process.return_value = DELIVERED
    return EventTriggerEngine(
        bus=bus,
        notifier=notifier,
        config=config or {},
        now_fn=now_fn or (lambda: _now()),
        weather=weather,
        schedule=schedule,
    ), notifier


# =====================================================================
# 1. 哀伤节奏 3/7/30 天触发断言（§3 / §7）
# =====================================================================
class TestGriefSchedule(unittest.TestCase):
    def test_grief_node_due_triggers_candidate(self) -> None:
        bus = EventBus()
        cfg = {"grief_schedule": [datetime(2026, 9, 10).date()]}  # 3/7/30 天节点（M1.6 喂入）
        eng, notifier = _make_engine(bus, None, cfg, now_fn=lambda: _now(10, 10, 9, 2026))
        status = eng.check_grief()
        notifier.process.assert_called_once()
        cand = notifier.process.call_args[0][0]
        self.assertEqual(cand["类型"], "哀伤节点")
        self.assertIn("内容草案", cand)
        self.assertIn("特征", cand)
        self.assertIn("relationship", cand["特征"])  # 走关系价值判定

    def test_grief_node_not_due_no_trigger(self) -> None:
        bus = EventBus()
        cfg = {"grief_schedule": [datetime(2026, 9, 10).date()]}
        eng, notifier = _make_engine(bus, None, cfg, now_fn=lambda: _now(10, 9, 9, 2026))
        status = eng.check_grief()
        self.assertIsNone(status)
        notifier.process.assert_not_called()

    def test_empty_grief_schedule_no_trigger(self) -> None:
        bus = EventBus()
        eng, notifier = _make_engine(bus, None, {"grief_schedule": []}, now_fn=lambda: _now())
        self.assertIsNone(eng.check_grief())
        notifier.process.assert_not_called()


# =====================================================================
# 2. 紧急穿透仅限用户配置清单断言（§5 / §7）
# =====================================================================
class TestEmergencyPassthrough(unittest.TestCase):
    def test_passthrough_when_type_in_config(self) -> None:
        bus = EventBus()
        cfg = {"emergency_passthrough": ["smoke_detected"]}
        eng, _ = _make_engine(bus, None, cfg)
        cand = {"类型": "环境异常", "紧急类型": "smoke_detected"}
        self.assertTrue(eng._is_emergency(cand))

    def test_no_passthrough_when_type_not_in_config(self) -> None:
        bus = EventBus()
        cfg = {"emergency_passthrough": ["smoke_detected"]}
        eng, _ = _make_engine(bus, None, cfg)
        cand = {"类型": "环境异常", "紧急类型": "door_open"}
        self.assertFalse(eng._is_emergency(cand))

    def test_no_passthrough_without_emergency_type(self) -> None:
        bus = EventBus()
        cfg = {"emergency_passthrough": ["smoke_detected"]}
        eng, _ = _make_engine(bus, None, cfg)
        self.assertFalse(eng._is_emergency({"类型": "环境异常"}))  # 无紧急类型 → 不穿透


# =====================================================================
# 3. 同类窗口聚合（防风暴）断言（§4.3 / §7）—— 经引擎路由层面验证
# =====================================================================
class TestEngineAggregation(unittest.TestCase):
    def test_same_event_within_window_triggers_once(self) -> None:
        bus = EventBus()
        # 隔离聚合层：禁用冷却，验证「同类窗口聚合只触发一次」
        cfg = {"urgency_map": {"环境异常": "high"}, "aggregate_window_seconds": 1800, "cooldown_seconds": 0}
        eng, notifier = _make_engine(
            bus, None, cfg, now_fn=lambda: _now(10, 10, 9, 2026)
        )
        # 同一类事件（同一传感器）在同一窗口内出现两次
        bus.emit("env.anomaly", {"传感器": "temperature", "数值": 45})
        bus.emit("env.anomaly", {"传感器": "temperature", "数值": 47})
        notifier.process.assert_called_once()  # 只触发一次

    def test_different_sensor_not_suppressed(self) -> None:
        bus = EventBus()
        # 隔离聚合层：禁用冷却，验证「不同 key（不同类事件）互不抑制」
        cfg = {"urgency_map": {"环境异常": "high"}, "aggregate_window_seconds": 1800, "cooldown_seconds": 0}
        eng, notifier = _make_engine(
            bus, None, cfg, now_fn=lambda: _now(10, 10, 9, 2026)
        )
        bus.emit("env.anomaly", {"传感器": "temperature", "数值": 45})
        bus.emit("env.anomaly", {"传感器": "smoke", "数值": 0.5})
        self.assertEqual(notifier.process.call_count, 2)  # 不同 key 不抑制


# =====================================================================
# 4. 事件到达 → 相关性/紧急度 → 生成候选 → 调 notifier.process()（§4.3）
# =====================================================================
class TestEventFlowToProcess(unittest.TestCase):
    def test_event_reaches_notifier_process(self) -> None:
        bus = EventBus()
        cfg = {"urgency_map": {"环境异常": "high"}}
        eng, notifier = _make_engine(
            bus, None, cfg, now_fn=lambda: _now(10, 10, 9, 2026)
        )
        bus.emit("env.anomaly", {"传感器": "temperature", "数值": 45})
        notifier.process.assert_called_once()
        cand = notifier.process.call_args[0][0]
        self.assertEqual(cand["类型"], "环境异常")
        self.assertIn("内容草案", cand)
        for k in ("urgency", "actionability", "relationship", "freshness"):
            self.assertIn(k, cand["特征"])

    def test_irrelevant_event_dropped(self) -> None:
        bus = EventBus()
        cfg = {"relevance_block": ["环境异常"]}  # 该类型判为不相关
        eng, notifier = _make_engine(bus, None, cfg, now_fn=lambda: _now())
        bus.emit("env.anomaly", {"传感器": "temperature", "数值": 45})
        notifier.process.assert_not_called()


# =====================================================================
# 5. 预判型（天气+日程两源）：天气降温 + 明日日程 → 生成预判候选（§4.3 / §8）
# =====================================================================
class TestPreemptiveTwoSource(unittest.TestCase):
    def test_cold_snap_plus_tomorrow_agenda_generates_candidate(self) -> None:
        bus = EventBus()
        eng, notifier = _make_engine(
            bus, None, {},
            now_fn=lambda: _now(20, 10, 9, 2026),
            weather=StubWeather(today=20.0, tomorrow_min=2.0),   # 大幅降温
            schedule=StubSchedule(agenda=[{"title": "渠道会"}]),
        )
        status = eng.check_preemptive()
        notifier.process.assert_called_once()
        cand = notifier.process.call_args[0][0]
        self.assertEqual(cand["类型"], "预判·加衣")
        self.assertIn("加衣", cand["内容草案"])  # 明早加衣提醒

    def test_no_cold_snap_no_candidate(self) -> None:
        bus = EventBus()
        eng, notifier = _make_engine(
            bus, None, {},
            now_fn=lambda: _now(20, 10, 9, 2026),
            weather=StubWeather(today=20.0, tomorrow_min=18.0),  # 不降温
            schedule=StubSchedule(agenda=[{"title": "渠道会"}]),
        )
        status = eng.check_preemptive()
        self.assertIsNone(status)
        notifier.process.assert_not_called()

    def test_cold_snap_but_no_tomorrow_agenda_no_candidate(self) -> None:
        bus = EventBus()
        eng, notifier = _make_engine(
            bus, None, {},
            now_fn=lambda: _now(20, 10, 9, 2026),
            weather=StubWeather(today=20.0, tomorrow_min=2.0),
            schedule=StubSchedule(agenda=[]),   # 明日无日程
        )
        self.assertIsNone(eng.check_preemptive())
        notifier.process.assert_not_called()


# =====================================================================
# 6. 订阅事件登记一致断言（§6 事件契约；不新增/不改白名单）
# =====================================================================
class TestSubscribedEventsRegistered(unittest.TestCase):
    def test_six_subscribed_events_all_in_whitelist(self) -> None:
        self.assertEqual(len(SUBSCRIBED_EVENTS), 6)
        for evt in SUBSCRIBED_EVENTS:
            self.assertIn(evt, EVENT_TYPES)

    def test_subscribed_names_match_registry(self) -> None:
        expected = {
            "schedule.anniversary",
            "env.anomaly",
            "device.state_changed",
            "macro.state_changed",
            "memory.profile_updated",
            "affect.updated",
        }
        self.assertEqual(set(SUBSCRIBED_EVENTS), expected)

    def test_engine_subscribes_all_six(self) -> None:
        bus = EventBus()
        eng, _ = _make_engine(bus, None, {})
        for evt in SUBSCRIBED_EVENTS:
            self.assertEqual(bus.count(evt), 1)


if __name__ == "__main__":
    unittest.main()
