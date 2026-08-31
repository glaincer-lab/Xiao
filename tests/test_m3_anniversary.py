"""M3-M4 纪念日豁免 + 画像异常侦测（backend/m3/anniversary.py）单元测试。

TDD 契约：先写验收断言再实现，覆盖 docs/specs/M3-proactive.md：
    sec 4.5 数字纪念日（v4 终裁）— 彻底废除纯计数型（第 N 句话）；保留两类穿透豁免：
          1 里程碑能力见证 2 正向极值见证（恢复/坚持/成就 only）→ is_witness_exempt(date)。
    sec 4.5 画像异常侦测纪律 — 负向异常（打破惯例）永不主动开场；
          仅 1 响应时 Context 增强 2 偏离惯例确认（这两个仅留行为语义，不实现对话干预）。
    sec 6 事件契约 — 订阅 memory.profile_updated（已登记，只订阅不新增）。

验收断言（6 条核心，见各 TestCase）：
    1. 纪念日穿透豁免：里程碑/正向极值 → is_witness_exempt True；纯计数型 → False。
    2. 候选生成：豁免 → relationship 0.9 爆表；纯计数型 → 废除（None）。
    3. 豁免候选走 M3-M1 process()：关系爆表不占额度（DELIVERED 但 budget 为 0）。
    4. 负向异常永不主动开场：惯例偏离 → 标记不主动开场（不生成候选/不投递）。
    5. 订阅事件登记一致：memory.profile_updated 已在 EVENT_TYPES 白名单。
    6. memory.profile_updated 事件刷新画像快照（只读不写）。

运行：python -m unittest tests.test_m3_anniversary -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.event_bus import EVENT_TYPES, EventBus
from backend.m3.score import RELATIONSHIP_BOOM_THRESHOLD
from backend.m3.notify import ProactiveNotifier, DELIVERED
from backend.m3.budget import ProactiveBudget
from backend.m3.anniversary import (
    SUBSCRIBED_EVENTS,
    WITNESS_MILESTONE,
    WITNESS_POSITIVE,
    WITNESS_COUNT,
    AnniversaryEngine,
    is_witness_exempt,
    is_negative_anomaly,
)


def _milestone_entry() -> dict:
    return {
        "类型": "纪念日",
        "事件": "第一次不问你就调好室温",
        "日期": "2026-09-10",
        "见证类别": WITNESS_MILESTONE,  # 里程碑能力见证（M6.1 双源入册）
    }


def _positive_entry() -> dict:
    return {
        "类型": "纪念日",
        "事件": "连续三周周末休息",
        "日期": "2026-09-10",
        "见证类别": WITNESS_POSITIVE,  # 正向极值见证（恢复/坚持/成就）
    }


def _count_entry() -> dict:
    return {
        "类型": "纪念日",
        "事件": "第 100 句话",
        "日期": "2026-09-10",
        "见证类别": WITNESS_COUNT,  # 纯计数型（废除）
    }


class _StubProfile:
    """M1 惯例画像 stub：habit_profile() 只读返回可配置快照。"""

    def __init__(self, profile: dict | None = None) -> None:
        self.profile = profile or {"mode": "normal", "rebuild_reason": "", "habits": []}

    def habit_profile(self) -> dict:
        return dict(self.profile)


# =====================================================================
# 1. 纪念日穿透豁免断言（sec 4.5 终裁）
# =====================================================================
class TestWitnessExemption(unittest.TestCase):
    def test_milestone_is_witness_exempt(self) -> None:
        self.assertTrue(is_witness_exempt(_milestone_entry()))

    def test_positive_extreme_is_witness_exempt(self) -> None:
        self.assertTrue(is_witness_exempt(_positive_entry()))

    def test_count_based_is_not_exempt(self) -> None:
        self.assertFalse(is_witness_exempt(_count_entry()))

    def test_unknown_witness_type_is_not_exempt(self) -> None:
        self.assertFalse(is_witness_exempt({"见证类别": "something_else"}))


# =====================================================================
# 2. 纪念日候选生成：豁免 -> relationship 爆表；纯计数型 -> 废除（None）
# =====================================================================
class TestAnniversaryCandidate(unittest.TestCase):
    def test_milestone_candidate_relationship_boom(self) -> None:
        eng = AnniversaryEngine(bus=EventBus())
        cand = eng.build_candidate(_milestone_entry())
        self.assertIsNotNone(cand)
        self.assertEqual(cand["特征"]["relationship"], 0.9)
        self.assertGreaterEqual(cand["特征"]["relationship"], RELATIONSHIP_BOOM_THRESHOLD)

    def test_positive_candidate_relationship_boom(self) -> None:
        eng = AnniversaryEngine(bus=EventBus())
        cand = eng.build_candidate(_positive_entry())
        self.assertIsNotNone(cand)
        self.assertGreaterEqual(cand["特征"]["relationship"], RELATIONSHIP_BOOM_THRESHOLD)

    def test_count_based_candidate_suppressed(self) -> None:
        eng = AnniversaryEngine(bus=EventBus())
        self.assertIsNone(eng.build_candidate(_count_entry()))

    def test_candidate_structure_matches_notify_contract(self) -> None:
        eng = AnniversaryEngine(bus=EventBus())
        cand = eng.build_candidate(_milestone_entry())
        self.assertIn("内容草案", cand)
        for k in ("urgency", "actionability", "relationship", "freshness"):
            self.assertIn(k, cand["特征"])


# =====================================================================
# 3. 豁免候选走 M3-M1 消费：关系爆表 -> 不占额度（sec 4.1 / sec 7）
# =====================================================================
class TestWitnessExemptQuota(unittest.TestCase):
    def test_boom_candidate_exempts_quota(self) -> None:
        budget = ProactiveBudget(daily_quota=3, persist_path=Path(tempfile.mkdtemp()) / "b.json")
        notifier = ProactiveNotifier(budget=budget)
        eng = AnniversaryEngine(bus=EventBus())
        cand = eng.build_candidate(_milestone_entry())
        status = notifier.process(cand)
        self.assertEqual(status, DELIVERED)
        self.assertEqual(budget.consumed_today, 0)  # 关系爆表豁免，不占额度

    def test_boom_candidate_still_respects_dormant_gate(self) -> None:
        budget = ProactiveBudget(daily_quota=3, persist_path=Path(tempfile.mkdtemp()) / "b.json")
        macro = mock.Mock()
        macro.is_proactive_allowed.return_value = False  # DORMANT
        notifier = ProactiveNotifier(budget=budget, macro=macro)
        eng = AnniversaryEngine(bus=EventBus())
        cand = eng.build_candidate(_milestone_entry())
        self.assertEqual(notifier.process(cand), "dropped_dormant")
        self.assertEqual(budget.consumed_today, 0)


# =====================================================================
# 4. 负向异常永不主动开场断言（sec 4.5 画像异常侦测纪律）
# =====================================================================
class TestNegativeAnomaly(unittest.TestCase):
    def test_is_negative_anomaly_detects_habit_break(self) -> None:
        profile = {"mode": "normal", "rebuild_reason": "",
                   "habits": [{"id": "h1", "content": "每晚 11 点前睡", "status": "active"}]}
        self.assertTrue(is_negative_anomaly({"habit_id": "h1", "status": "missed"}, profile))

    def test_is_negative_anomaly_ok_when_habit_kept(self) -> None:
        profile = {"mode": "normal", "rebuild_reason": "",
                   "habits": [{"id": "h1", "content": "每晚 11 点前睡", "status": "active"}]}
        self.assertFalse(is_negative_anomaly({"habit_id": "h1", "status": "kept"}, profile))

    def test_negative_anomaly_marks_no_proactive(self) -> None:
        eng = AnniversaryEngine(bus=EventBus(), profile=_StubProfile({
            "mode": "normal", "rebuild_reason": "",
            "habits": [{"id": "h1", "content": "每晚 11 点前睡", "status": "active"}],
        }))
        self.assertTrue(eng.detect_negative_anomaly({"habit_id": "h1", "status": "missed"}))
        self.assertTrue(eng.negative_anomaly)
        self.assertFalse(eng.should_open())  # 不主动开场

    def test_negative_anomaly_suppresses_candidate(self) -> None:
        eng = AnniversaryEngine(bus=EventBus(), profile=_StubProfile({
            "mode": "normal", "rebuild_reason": "",
            "habits": [{"id": "h1", "content": "每晚 11 点前睡", "status": "active"}],
        }))
        eng.detect_negative_anomaly({"habit_id": "h1", "status": "missed"})
        self.assertIsNone(eng.build_candidate(_milestone_entry()))

    def test_no_anomaly_opens(self) -> None:
        eng = AnniversaryEngine(bus=EventBus(), profile=_StubProfile({
            "mode": "normal", "rebuild_reason": "",
            "habits": [{"id": "h1", "content": "每晚 11 点前睡", "status": "active"}],
        }))
        self.assertFalse(eng.detect_negative_anomaly({"habit_id": "h1", "status": "kept"}))
        self.assertTrue(eng.should_open())
        self.assertIsNotNone(eng.build_candidate(_milestone_entry()))


# =====================================================================
# 5. 订阅事件登记一致断言（sec 6：只订阅已登记事件，不新增/不改白名单）
# =====================================================================
class TestSubscribedEventsRegistered(unittest.TestCase):
    def test_subscribed_memory_profile_updated_in_whitelist(self) -> None:
        self.assertEqual(SUBSCRIBED_EVENTS, ("memory.profile_updated",))
        for evt in SUBSCRIBED_EVENTS:
            self.assertIn(evt, EVENT_TYPES)

    def test_engine_subscribes_memory_profile_updated(self) -> None:
        bus = EventBus()
        eng = AnniversaryEngine(bus=bus)
        self.assertEqual(bus.count("memory.profile_updated"), 1)
        eng.close()
        self.assertEqual(bus.count("memory.profile_updated"), 0)


# =====================================================================
# 6. memory.profile_updated 刷新画像快照（供负向异常侦测；只读不写）
# =====================================================================
class TestProfileUpdatedRefresh(unittest.TestCase):
    def test_profile_updated_event_refreshes_snapshot(self) -> None:
        bus = EventBus()
        profile = _StubProfile({
            "mode": "normal", "rebuild_reason": "",
            "habits": [{"id": "h1", "content": "每晚 11 点前睡", "status": "active"}],
        })
        eng = AnniversaryEngine(bus=bus, profile=profile)
        bus.emit("memory.profile_updated", {"版本": "v2", "变更字段": ["habits"]})
        self.assertTrue(eng.detect_negative_anomaly({"habit_id": "h1", "status": "missed"}))


if __name__ == "__main__":
    unittest.main()
