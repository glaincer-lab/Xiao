"""M1-B 冲突三协议 + 检索合并 + 周配额 的单元测试。

按 MEMV1_CONTRACT.md 五要素 schema 自建最小 MemEntry 桩 dataclass（不硬依赖
``backend.memv1.schema``，因 M1-A 并行实现可能尚未落地）。被测逻辑针对字段名与
契约一致的 dict/dataclass 工作。
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from backend.memv1.conflict import (
    BEHAVIOR_CONFIRM_THRESHOLD,
    WeeklyQuota,
    classify_conflict,
    merge_for_retrieval,
)


@dataclass
class MemEntry:
    """最小 MemEntry 桩：字段与 MEMV1_CONTRACT.md 五要素 schema 一致。"""

    id: str = ""
    content: str = ""
    scope: str = "global"
    scope_detail: dict | None = field(default=None)
    effective_at: str = ""
    source: str = "explicit"
    status: str = "active"
    confirmed: bool = False
    affective_luminance: int = 0
    confidence: float = 0.0
    encrypted: bool = False
    enc_token: str = ""


class TestClassifyConflict(unittest.TestCase):
    """冲突三协议：沉淀（高/低）、使用（行为连续 3 次）、身份级。"""

    def test_sedimentation_high_value_asks_clarify(self):
        """沉淀冲突：高价值偏好 -> 顺口澄清（high）。"""
        new = MemEntry(content="不爱吃辣", source="explicit", confirmed=True,
                       affective_luminance=4, scope="global")
        existing = [MemEntry(content="喜欢吃辣", source="explicit", scope="global")]
        self.assertEqual(classify_conflict(new, existing), "high")

    def test_sedimentation_low_value_stores_as_period(self):
        """沉淀冲突：低价值 -> 按 scope=period 保守存（low）。"""
        new = MemEntry(content="偶尔喝咖啡", source="inferred",
                       confidence=0.2, affective_luminance=0, scope="global")
        existing = [MemEntry(content="完全不喝咖啡", source="explicit", scope="global")]
        self.assertEqual(classify_conflict(new, existing), "low")

    def test_usage_below_threshold_never_accuse(self):
        """使用冲突：行为违背 <3 次 -> 直接服务绝不指责（low）。"""
        declared = MemEntry(content="不喝咖啡", source="explicit", scope="global")
        prior1 = MemEntry(content="昨天喝了咖啡", source="behavior", scope="global")
        new = MemEntry(content="今天又喝了咖啡", source="behavior", scope="global")
        result = classify_conflict(new, [declared, prior1])
        self.assertEqual(result, "low")
        self.assertLess(2, BEHAVIOR_CONFIRM_THRESHOLD)  # 2 次仍未到 3 次

    def test_usage_confirm_after_consecutive_violations(self):
        """使用冲突：行为连续 3 次违背同一记忆 -> 温和确认一次（high）。"""
        declared = MemEntry(content="不喝咖啡", source="explicit", scope="global")
        prior1 = MemEntry(content="前天喝了咖啡", source="behavior", scope="global")
        prior2 = MemEntry(content="昨天喝了咖啡", source="behavior", scope="global")
        new = MemEntry(content="今天又喝了咖啡", source="behavior", scope="global")
        self.assertEqual(classify_conflict(new, [declared, prior1, prior2]), "high")

    def test_identity_migration(self):
        """身份级变化：搬家/换城市 -> 记事件 + 一次批量迁移确认（identity）。"""
        new = MemEntry(content="搬到上海了", source="explicit", scope="event")
        existing = [MemEntry(content="默认生活在上海", source="explicit", scope="global")]
        self.assertEqual(classify_conflict(new, existing), "identity")

    def test_no_conflict_returns_none(self):
        """无同类槽位/无实质矛盾 -> none。"""
        new = MemEntry(content="喜欢猫", source="explicit", scope="global")
        existing = [MemEntry(content="喜欢狗", source="explicit", scope="global")]
        self.assertEqual(classify_conflict(new, existing), "none")

    def test_agreeing_statement_not_a_conflict(self):
        """同槽位但同向（不矛盾）-> none。"""
        new = MemEntry(content="喜欢喝咖啡", source="explicit", scope="global")
        existing = [MemEntry(content="爱喝咖啡", source="explicit", scope="global")]
        self.assertEqual(classify_conflict(new, existing), "none")


class TestMergeForRetrieval(unittest.TestCase):
    """检索合并：局部>全局、新>旧、行为>声明。"""

    def test_local_before_global(self):
        local = MemEntry(content="常去静安寺旁那家咖啡店", scope="place",
                         effective_at="2026-08-30", source="explicit")
        global_ = MemEntry(content="默认生活在上海", scope="global",
                           effective_at="2026-08-30", source="explicit")
        result = merge_for_retrieval([global_, local])
        self.assertEqual(result[0].scope, "place")
        self.assertEqual(result[-1].scope, "global")

    def test_behavior_over_declaration(self):
        """同一件事上：行为 > 声明（行为替代声明）。"""
        declaration = MemEntry(content="不喝咖啡", source="explicit", scope="global",
                               effective_at="2026-08-30")
        behavior = MemEntry(content="喝了咖啡", source="behavior", scope="global",
                            effective_at="2026-08-30")
        result = merge_for_retrieval([declaration, behavior])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].source, "behavior")

    def test_newer_before_older(self):
        old = MemEntry(content="喜欢猫", source="explicit", scope="global",
                       effective_at="2025-01-01")
        new = MemEntry(content="喜欢狗", source="explicit", scope="global",
                       effective_at="2026-01-01")
        result = merge_for_retrieval([old, new])
        self.assertEqual(result[0].content, "喜欢狗")

    def test_pending_clarify_returns_declaration(self):
        """待澄清：检索默认返回声明，而非行为条。"""
        declaration = MemEntry(content="不喝咖啡", source="explicit", scope="global",
                               status="pending_clarify", effective_at="2026-08-30")
        behavior = MemEntry(content="喝了咖啡", source="behavior", scope="global",
                            status="pending_clarify", effective_at="2026-08-30")
        result = merge_for_retrieval([declaration, behavior])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].content, "不喝咖啡")
        self.assertEqual(result[0].source, "explicit")


class _FakeClock:
    """可变的假时钟，用于测试跨周重置。"""

    def __init__(self, dt: datetime):
        self.dt = dt

    def __call__(self) -> datetime:
        return self.dt


class TestWeeklyQuota(unittest.TestCase):
    """周配额：分项上限、全局约束、优先级（澄清>体检）、跨周重置、超限返回 False。"""

    MONDAY = datetime(2026, 9, 7, 12, 0)  # 周一

    def _quota(self, memory_budget=None, global_budget=None):
        clock = _FakeClock(self.MONDAY)
        return WeeklyQuota(now_fn=clock, memory_budget=memory_budget, global_budget=global_budget), clock

    def test_clarify_exhausts_combined_budget(self):
        """记忆澄清合计每周 ≤3；超限返回 False。"""
        q, _ = self._quota()
        self.assertTrue(q.try_consume("clarify"))
        self.assertTrue(q.try_consume("clarify"))
        self.assertTrue(q.try_consume("clarify"))
        self.assertFalse(q.try_consume("clarify"))  # 超限
        self.assertEqual(q.remaining(), 0)

    def test_combined_clarify_and_periodic_over_limit(self):
        """澄清+周期体检共享每周 3 池；池满后任一再次消费返回 False。"""
        q, _ = self._quota()
        self.assertTrue(q.try_consume("clarify"))         # 1/3
        self.assertTrue(q.try_consume("periodic_check"))  # 2/3
        self.assertTrue(q.try_consume("clarify"))         # 3/3
        self.assertFalse(q.try_consume("clarify"))        # 池满，超限
        self.assertFalse(q.try_consume("periodic_check"))  # 池满，超限
        self.assertEqual(q.remaining(), 0)

    def test_periodic_yields_to_clarify_when_tight(self):
        """只剩 1 个槽位时留给冲突澄清；体检顺延。"""
        q, _ = self._quota()
        self.assertTrue(q.try_consume("clarify"))
        self.assertTrue(q.try_consume("clarify"))
        self.assertFalse(q.try_consume("periodic_check"))  # 槽位留给澄清
        self.assertTrue(q.try_consume("clarify"))           # 澄清可用最后一槽
        self.assertFalse(q.try_consume("periodic_check"))   # 已满，体检顺延

    def test_global_budget_bounds(self):
        """整体受全局 ≤5/周约束：超过返回 False。"""
        q, _ = self._quota(memory_budget=10, global_budget=5)
        for _ in range(5):
            self.assertTrue(q.try_consume("clarify"))
        self.assertFalse(q.try_consume("clarify"))  # 全局超限

    def test_unknown_kind_rejected(self):
        q, _ = self._quota()
        self.assertFalse(q.try_consume("bogus"))

    def test_reset_on_new_week(self):
        q, clock = self._quota()
        for _ in range(3):
            self.assertTrue(q.try_consume("clarify"))
        self.assertFalse(q.try_consume("clarify"))
        self.assertEqual(q.remaining(), 0)
        # 进入下一周
        clock.dt = self.MONDAY + timedelta(days=7)
        self.assertEqual(q.remaining(), 3)
        self.assertTrue(q.try_consume("clarify"))

    def test_remaining_decrements(self):
        q, _ = self._quota()
        self.assertEqual(q.remaining(), 3)
        q.try_consume("clarify")
        self.assertEqual(q.remaining(), 2)
        q.try_consume("periodic_check")
        self.assertEqual(q.remaining(), 1)


if __name__ == "__main__":
    unittest.main()
