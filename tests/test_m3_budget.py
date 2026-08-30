"""M3-M1 预算制候选消费（backend/m3/）单元测试。

TDD 契约：先写验收断言再实现满足。覆盖 docs/specs/M3-proactive.md §7 模块级断言：
1. 日额度硬上限（任何路径消费不超 quota；含预消费/并发/跨天边界；用满后拒绝）
2. 全屏期零投递（attention.fullscreen on → 候选丢弃不攒、零投递）
3. proactive.candidate / proactive.delivered 发布断言（bus.on 捕获 payload）
4. 打分权重与总分归一化（四维权重正确，总分 ∈ [0,1]）
5. 豁免穿透不占额度（关系爆表 → 不消费 quota）
6. ≤0.6 静默丢弃（不发布 delivered、记日志）
7. DORMANT（macro_state.is_proactive_allowed False）→ 不消费不投递

运行：python -m unittest tests.test_m3_budget -v
"""
from __future__ import annotations

import json
import shutil
import threading
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from backend.event_bus import EventBus
from backend.m3.budget import DEFAULT_DAILY_QUOTA, ProactiveBudget, QuotaExceededError
from backend.m3.notify import (
    DELIVERED,
    DROPPED_DND,
    DROPPED_DORMANT,
    DROPPED_FULLSCREEN,
    DROPPED_QUOTA_EXCEEDED,
    DROPPED_SCORE_LOW,
    ProactiveNotifier,
)
from backend.m3.score import (
    DIMS,
    RELATIONSHIP_BOOM_THRESHOLD,
    TOTAL_THRESHOLD,
    WEIGHTS,
    is_relationship_boom,
    normalize,
    relationship_decay,
    score_candidate,
    total_score,
)

# 项目根下可写临时目录（.tmp 已被 .gitignore 忽略；系统 Temp 会被沙箱拒写，
# 这正是 test_memory 既有的沙箱失败根因，本测试规避之）。
_PROJECT_TMP = Path(__file__).resolve().parent.parent / ".tmp"


def _tmp_dir(prefix: str) -> Path:
    d = _PROJECT_TMP / f"{prefix}_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---- 可注入替身（不复用真实 attention/macro_state，避免硬件/全局态污染） ----
class FakeSensor:
    def __init__(self, fullscreen: bool = False, idle: bool = False) -> None:
        self._fullscreen = bool(fullscreen)
        self._idle = bool(idle)

    def is_fullscreen(self, hwnd=None) -> bool:
        return self._fullscreen

    def is_idle(self, threshold: float = 15 * 60) -> bool:
        return self._idle


class FakeMacro:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = bool(allowed)

    def is_proactive_allowed(self) -> bool:
        return self.allowed


def _candidate(features: dict, ctype: str = "心跳", draft: str = "要不要我帮你看看明天的安排？", **kw) -> dict:
    c = {"类型": ctype, "内容草案": draft, "特征": features}
    c.update(kw)
    return c


_HIGH = {"urgency": 0.9, "actionability": 0.9, "relationship": 0.5, "freshness": 0.8}


class BudgetBase(unittest.TestCase):
    def setUp(self) -> None:
        self._cleanup_dirs: list[Path] = []

    def tearDown(self) -> None:
        for d in self._cleanup_dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _budget(self, quota: int | None = None, day: datetime | None = None) -> ProactiveBudget:
        """构造一个落在可写临时目录、且 now_fn 可控的预算实例。"""
        d = _tmp_dir("budget")
        self._cleanup_dirs.append(d)
        now_ref = {"t": day or datetime(2026, 8, 30, 12, 0, 0)}
        b = ProactiveBudget(
            daily_quota=DEFAULT_DAILY_QUOTA if quota is None else quota,
            persist_path=d / "budget.json",
            now_fn=lambda: now_ref["t"],
        )
        b._now_ref = now_ref  # 供跨天测试手动改时（非公开 API，仅测试用）
        return b

    def _notifier(self, budget=None, sensor=None, macro=None, bus=None, config=None) -> ProactiveNotifier:
        config = dict(config or {})
        config.setdefault("cooldown_seconds", 0)
        config.setdefault("attention_policy", "off")
        return ProactiveNotifier(
            budget=budget if budget is not None else self._budget(),
            sensor=sensor if sensor is not None else FakeSensor(),
            macro=macro if macro is not None else FakeMacro(True),
            bus=bus if bus is not None else EventBus(),
            config=config,
        )


# =====================================================================
# 1. 日额度硬上限断言（§7）
# =====================================================================
class TestDailyQuotaHardCap(BudgetBase):
    def test_default_quota_is_three(self) -> None:
        self.assertEqual(DEFAULT_DAILY_QUOTA, 3)
        b = self._budget()
        self.assertEqual(b.daily_quota, 3)
        self.assertEqual(b.consumed_today, 0)
        self.assertEqual(b.remaining, 3)

    def test_consume_respects_cap_and_denies_overtop(self) -> None:
        b = self._budget(quota=3)
        for _ in range(3):
            self.assertTrue(b.can_consume(1))
            b.consume(1)
        self.assertEqual(b.consumed_today, 3)
        self.assertEqual(b.remaining, 0)
        self.assertFalse(b.can_consume(1))
        with self.assertRaises(QuotaExceededError):
            b.consume(1)
        self.assertEqual(b.consumed_today, 3)

    def test_quota_never_exceeded_under_concurrency(self) -> None:
        b = self._budget(quota=3)
        success: list[int] = []
        denied: list[int] = []
        barrier = threading.Barrier(16)

        def worker() -> None:
            barrier.wait()
            try:
                b.consume(1)
                success.append(1)
            except QuotaExceededError:
                denied.append(1)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(len(success), 3)
        self.assertEqual(len(denied), 13)
        self.assertEqual(b.consumed_today, 3)
        self.assertFalse(b.can_consume())

    def test_batch_consume_exceeding_quota_raises(self) -> None:
        b = self._budget(quota=2)
        self.assertTrue(b.can_consume(2))
        b.consume(2)
        self.assertEqual(b.consumed_today, 2)
        self.assertFalse(b.can_consume(1))
        with self.assertRaises(QuotaExceededError):
            b.consume(1)

    def test_cross_day_resets_consumed(self) -> None:
        b = self._budget(quota=3)
        b.consume(2)
        self.assertEqual(b.consumed_today, 2)
        # 推进到次日 → 自动归零
        b._now_ref["t"] = datetime(2026, 8, 31, 9, 0, 0)
        self.assertEqual(b.consumed_today, 0)
        self.assertTrue(b.can_consume(3))
        self.assertEqual(b.remaining, 3)

    def test_set_daily_quota_clamps_and_persists(self) -> None:
        d = _tmp_dir("budget")
        self._cleanup_dirs.append(d)
        b = ProactiveBudget(persist_path=d / "b.json")
        b.set_daily_quota(5)
        self.assertEqual(b.daily_quota, 5)
        b.set_daily_quota(-2)
        self.assertEqual(b.daily_quota, 0)
        # 读回：从磁盘恢复 injected 配额与已消费
        b2 = ProactiveBudget(persist_path=d / "b.json")
        self.assertEqual(b2.daily_quota, 0)

    def test_budget_persists_and_reloads(self) -> None:
        d = _tmp_dir("budget")
        self._cleanup_dirs.append(d)
        b1 = ProactiveBudget(daily_quota=4, persist_path=d / "b.json")
        b1.consume(2)
        b2 = ProactiveBudget(persist_path=d / "b.json")
        self.assertEqual(b2.daily_quota, 4)
        self.assertEqual(b2.consumed_today, 2)

    def test_notify_drops_when_quota_exhausted(self) -> None:
        b = self._budget(quota=1)
        b.consume(1)  # 用满
        bus = EventBus()
        notif = self._notifier(budget=b, bus=bus)
        status = notif.process(_candidate(_HIGH))
        self.assertEqual(status, DROPPED_QUOTA_EXCEEDED)
        self.assertEqual(b.consumed_today, 1)


# =====================================================================
# 2. 全屏期零投递断言（§7）
# =====================================================================
class TestFullscreenFreeze(BudgetBase):
    def test_fullscreen_drops_candidate_zero_delivery(self) -> None:
        bus = EventBus()
        candidates: list[dict] = []
        delivered: list[dict] = []
        bus.on("proactive.candidate", lambda p: candidates.append(p))
        bus.on("proactive.delivered", lambda p: delivered.append(p))
        b = self._budget(quota=3)
        notif = self._notifier(budget=b, sensor=FakeSensor(fullscreen=True), bus=bus)
        status = notif.process(_candidate(_HIGH))
        self.assertEqual(status, DROPPED_FULLSCREEN)
        self.assertEqual(candidates, [])
        self.assertEqual(delivered, [])
        self.assertEqual(b.consumed_today, 0)  # 丢弃不攒、不占额度
        self.assertEqual(b.remaining, 3)


# =====================================================================
# 3. proactive.candidate / proactive.delivered 发布断言（§6 / §7）
# =====================================================================
class TestEventPublishing(BudgetBase):
    def test_publishes_candidate_and_delivered(self) -> None:
        bus = EventBus()
        candidates: list[dict] = []
        delivered: list[dict] = []
        bus.on("proactive.candidate", lambda p: candidates.append(p))
        bus.on("proactive.delivered", lambda p: delivered.append(p))
        b = self._budget(quota=3)
        notif = self._notifier(budget=b, bus=bus)
        status = notif.process(_candidate(_HIGH, ctype="事件触发", draft="今晚有雨，记得带伞。"))
        self.assertEqual(status, DELIVERED)

        self.assertEqual(len(candidates), 1)
        cp = candidates[0]
        self.assertEqual(set(cp.keys()), {"类型", "四维分", "内容草案"})
        self.assertEqual(cp["类型"], "事件触发")
        self.assertEqual(cp["内容草案"], "今晚有雨，记得带伞。")
        for k in DIMS:
            self.assertIn(k, cp["四维分"])
        self.assertIn("total", cp["四维分"])

        self.assertEqual(len(delivered), 1)
        dp = delivered[0]
        self.assertEqual(set(dp.keys()), {"id", "用户响应"})
        self.assertTrue(dp["id"])
        self.assertEqual(dp["用户响应"], "")
        # 消费了 1 条额度
        self.assertEqual(b.consumed_today, 1)

    def test_delivered_carries_provided_user_response(self) -> None:
        bus = EventBus()
        delivered: list[dict] = []
        bus.on("proactive.delivered", lambda p: delivered.append(p))
        b = self._budget(quota=3)
        notif = self._notifier(budget=b, bus=bus)
        status = notif.process(_candidate(_HIGH, **{"用户响应": "好的"}))
        self.assertEqual(status, DELIVERED)
        self.assertEqual(delivered[0]["用户响应"], "好的")


# =====================================================================
# 4. 打分权重与总分归一化（§3 / §7）
# =====================================================================
class TestFourDimScore(BudgetBase):
    def test_weights_sum_to_one(self) -> None:
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0, places=6)
        self.assertEqual(set(WEIGHTS.keys()), set(DIMS))

    def test_total_normalized_zero_to_one(self) -> None:
        self.assertAlmostEqual(
            score_candidate({"urgency": 1.0, "actionability": 1.0, "relationship": 1.0, "freshness": 1.0})["total"],
            1.0,
        )
        self.assertAlmostEqual(
            score_candidate({"urgency": 0.0, "actionability": 0.0, "relationship": 0.0, "freshness": 0.0})["total"],
            0.0,
        )
        self.assertAlmostEqual(
            score_candidate({"urgency": 0.5, "actionability": 0.5, "relationship": 0.5, "freshness": 0.5})["total"],
            0.5,
        )

    def test_clamps_out_of_range(self) -> None:
        self.assertAlmostEqual(
            score_candidate({"urgency": 2.0, "actionability": 2.0, "relationship": 2.0, "freshness": 2.0})["total"],
            1.0,
        )
        self.assertAlmostEqual(
            score_candidate({"urgency": -1.0, "actionability": -1.0, "relationship": -1.0, "freshness": -1.0})["total"],
            0.0,
        )

    def test_weight_applied_to_each_dimension(self) -> None:
        # 仅 urgency=1 → 总分 = 0.35
        self.assertAlmostEqual(
            score_candidate({"urgency": 1.0, "actionability": 0.0, "relationship": 0.0, "freshness": 0.0})["total"],
            0.35,
        )
        # 仅 relationship=1 → 总分 = 0.25
        self.assertAlmostEqual(
            score_candidate({"urgency": 0.0, "actionability": 0.0, "relationship": 1.0, "freshness": 0.0})["total"],
            0.25,
        )

    def test_normalize_and_total_score_helpers(self) -> None:
        self.assertEqual(normalize(0.5), 0.5)
        self.assertEqual(normalize(1.5), 1.0)
        self.assertEqual(normalize(-0.2), 0.0)
        self.assertAlmostEqual(total_score({"urgency": 1.0, "actionability": 0.0, "relationship": 0.0, "freshness": 0.0}), 0.35)

    def test_relationship_boom_and_decay(self) -> None:
        self.assertTrue(is_relationship_boom({"relationship": 0.95}))
        self.assertFalse(is_relationship_boom({"relationship": 0.5}))
        self.assertEqual(relationship_decay(1.0, 0.0), 1.0)
        self.assertGreater(relationship_decay(1.0, 7.0), 0.4)   # 一个半衰期 ≈0.5
        self.assertLess(relationship_decay(1.0, 21.0), 0.2)     # 三个半衰期 ≈0.125
        self.assertAlmostEqual(relationship_decay(1.0, 7.0, half_life_days=1.0), 1.0 * 0.5 ** 7, places=3)


# =====================================================================
# 5. 豁免穿透不占额度（§4.1 / §7）
# =====================================================================
class TestRelationshipBoomExemption(BudgetBase):
    def test_boom_exempt_no_quota(self) -> None:
        bus = EventBus()
        delivered: list[dict] = []
        bus.on("proactive.delivered", lambda p: delivered.append(p))
        b = self._budget(quota=3)
        notif = self._notifier(budget=b, bus=bus)
        # relationship=1.0 → 爆表豁免；总分=0.35*0.5+0.30*0.5+0.25*1.0+0.10*0.5=0.625>0.6
        status = notif.process(_candidate({"urgency": 0.5, "actionability": 0.5, "relationship": 1.0, "freshness": 0.5}, ctype="纪念日"))
        self.assertEqual(status, DELIVERED)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(b.consumed_today, 0)  # 不占额度
        self.assertEqual(b.remaining, 3)

    def test_boom_still_respects_dnd(self) -> None:
        # 豁免只免额度，不免勿扰（§4.1 流程：豁免穿透 → 勿扰检查 → 开口）
        bus = EventBus()
        delivered: list[dict] = []
        bus.on("proactive.delivered", lambda p: delivered.append(p))
        b = self._budget(quota=3)
        notif = self._notifier(budget=b, bus=bus, sensor=FakeSensor(idle=True), config={"attention_policy": "block_when_idle"})
        status = notif.process(_candidate({"urgency": 0.5, "actionability": 0.5, "relationship": 1.0, "freshness": 0.5}, ctype="纪念日"))
        self.assertEqual(status, DROPPED_DND)
        self.assertEqual(len(delivered), 0)
        self.assertEqual(b.consumed_today, 0)


# =====================================================================
# 6. ≤0.6 静默丢弃（§4.1 / §7）
# =====================================================================
class TestSilentDropBelowThreshold(BudgetBase):
    def test_below_threshold_silent_drop(self) -> None:
        bus = EventBus()
        candidates: list[dict] = []
        delivered: list[dict] = []
        bus.on("proactive.candidate", lambda p: candidates.append(p))
        bus.on("proactive.delivered", lambda p: delivered.append(p))
        b = self._budget(quota=3)
        notif = self._notifier(budget=b, bus=bus)
        with self.assertLogs("m3.notify", level="INFO"):
            status = notif.process(_candidate({"urgency": 0.1, "actionability": 0.1, "relationship": 0.1, "freshness": 0.1}))
        self.assertEqual(status, DROPPED_SCORE_LOW)
        self.assertEqual(candidates, [])   # 静默：无 candidate 事件
        self.assertEqual(delivered, [])    # 不发布 delivered
        self.assertEqual(b.consumed_today, 0)

    def test_threshold_boundary_exclusive(self) -> None:
        # >0.6 才消费；恰为 0.6 → 静默丢弃
        b = self._budget(quota=3)
        notif = self._notifier(budget=b)
        # 构造总分恰为 0.6：0.35*1 + 0.30*0 + 0.25*1 + 0.10*0 = 0.60
        status = notif.process(_candidate({"urgency": 1.0, "actionability": 0.0, "relationship": 1.0, "freshness": 0.0}))
        self.assertEqual(status, DROPPED_SCORE_LOW)
        self.assertEqual(b.consumed_today, 0)


# =====================================================================
# 7. DORMANT 联动 → 不消费不投递（§4.4 / §7）
# =====================================================================
class TestDormantFreeze(BudgetBase):
    def test_dormant_no_consume_no_deliver(self) -> None:
        bus = EventBus()
        candidates: list[dict] = []
        delivered: list[dict] = []
        bus.on("proactive.candidate", lambda p: candidates.append(p))
        bus.on("proactive.delivered", lambda p: delivered.append(p))
        b = self._budget(quota=3)
        notif = self._notifier(budget=b, macro=FakeMacro(False), bus=bus)
        status = notif.process(_candidate(_HIGH))
        self.assertEqual(status, DROPPED_DORMANT)
        self.assertEqual(candidates, [])
        self.assertEqual(delivered, [])
        self.assertEqual(b.consumed_today, 0)
        self.assertEqual(b.remaining, 3)


# =====================================================================
# 附加：勿扰时间窗口 / 紧急穿透（可配清单）
# =====================================================================
class TestDndAndEmergency(BudgetBase):
    def test_dnd_hours_blocks(self) -> None:
        bus = EventBus()
        delivered: list[dict] = []
        bus.on("proactive.delivered", lambda p: delivered.append(p))
        b = self._budget(quota=3)
        now = [1020.0]  # epoch ~ 00:17，落在 0~7 时勿扰窗
        notif = ProactiveNotifier(
            budget=b, sensor=FakeSensor(), macro=FakeMacro(True), bus=bus,
            config={"cooldown_seconds": 0, "attention_policy": "off", "dnd_hours": [(0, 7)]},
            now_fn=lambda: now[0],
        )
        status = notif.process(_candidate(_HIGH))
        self.assertEqual(status, DROPPED_DND)
        self.assertEqual(len(delivered), 0)

    def test_global_cooldown_blocks(self) -> None:
        bus = EventBus()
        delivered: list[dict] = []
        bus.on("proactive.delivered", lambda p: delivered.append(p))
        b = self._budget(quota=3)
        now = [1000.0]
        notif = ProactiveNotifier(
            budget=b, sensor=FakeSensor(), macro=FakeMacro(True), bus=bus,
            config={"cooldown_seconds": 600, "attention_policy": "off"},
            now_fn=lambda: now[0],
        )
        self.assertEqual(notif.process(_candidate(_HIGH)), DELIVERED)      # 第 1 次投递
        now[0] = 1010.0                                                     # 10 秒后仍在冷却窗内
        self.assertEqual(notif.process(_candidate(_HIGH, ctype="心跳2")), DROPPED_DND)
        self.assertEqual(len(delivered), 1)

    def test_dnd_hit_does_not_consume_quota(self) -> None:
        """勿扰命中 → 额度未消费（consumed_today 不变）+ 未 emit delivered（§4.1 前置语义）。"""
        bus = EventBus()
        delivered: list[dict] = []
        bus.on("proactive.delivered", lambda p: delivered.append(p))
        b = self._budget(quota=3)
        now = [1000.0]
        notif = ProactiveNotifier(
            budget=b, sensor=FakeSensor(), macro=FakeMacro(True), bus=bus,
            config={"cooldown_seconds": 600, "attention_policy": "off"},
            now_fn=lambda: now[0],
        )
        # 第 1 条投递成功，消费 1 条额度
        self.assertEqual(notif.process(_candidate(_HIGH, ctype="第一条")), DELIVERED)
        self.assertEqual(b.consumed_today, 1)
        # 冷却窗内第 2 条命中勿扰 → 不消费、不投递
        now[0] = 1010.0
        self.assertEqual(notif.process(_candidate(_HIGH, ctype="第二条")), DROPPED_DND)
        self.assertEqual(b.consumed_today, 1)   # 未再消费（保持 1，未变成 2）
        self.assertEqual(len(delivered), 1)     # 未 emit 第 2 条 delivered

    def test_emergency_passthrough_breaks_quota_and_dnd(self) -> None:
        bus = EventBus()
        delivered: list[dict] = []
        bus.on("proactive.delivered", lambda p: delivered.append(p))
        b = self._budget(quota=1)
        b.consume(1)  # 额度已满，但紧急穿透不受额度约束
        notif = self._notifier(budget=b, bus=bus, config={"cooldown_seconds": 600, "attention_policy": "block_when_idle", "emergency_passthrough": ["火警"]})
        status = notif.process(_candidate(_HIGH, **{"紧急类型": "火警"}))
        self.assertEqual(status, DELIVERED)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(b.consumed_today, 1)  # 紧急不占额度
