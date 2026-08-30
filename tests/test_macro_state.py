"""宏观在场状态机（backend/macro_state.py）单元测试（M0·T5）。

TDD 契约：先写 DORMANT 三条纪律与状态机/事件/RETURNING 模板断言，再实现满足。
覆盖：
- 三纪律 ① DORMANT 主动事件总线零消息 ② 零归因（无「用户是不是讨厌我」类文本）
  ③ 托付后台任务照办但不推送（进展只进回归简报）
- 状态机 ACTIVE─15min─IDLE─7天─DORMANT─用户主动对话─RETURNING─新交互─ACTIVE
- 事件 macro.state_changed {前态,后态,时长} 发布（含决策4.3/4.5）
- RETURNING 分层问候模板（≤3天/≤2周/≥2个月三档 + 三源简报预算）
"""
from __future__ import annotations

import shutil
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from backend.event_bus import EVENT_TYPES, EventBus
from backend.macro_state import (
    ACTIVE,
    DORMANT,
    IDLE,
    RETURNING,
    DORMANT_AFTER_SECONDS,
    IDLE_AFTER_SECONDS,
    MacroStateMachine,
    TIER_BRIEF_MAX,
    TIER_GREETING,
    TIER_TAIL,
    build_returning_brief,
    greeting_tier,
    returning_greeting,
)


def _ago(**kwargs) -> datetime:
    """返回 now 之前的某个时间点（naive）。"""
    return datetime.now() - timedelta(**kwargs)


# 零归因禁词（测试独立内联，不依赖实现常量，锁纪律②）
_ZERO_ATTRIBUTION_BANNED = (
    "讨厌我", "不爱我", "嫌弃", "烦我", "不理我", "是不是讨厌",
    "是不是我哪里", "被讨厌", "不爱理", "怎么看都不喜欢", "我好失败",
)


class MacroStateMachineTest(unittest.TestCase):
    def setUp(self) -> None:
        # 独立总线 + 事件记录，避免污染全局单例 bus
        self.bus = EventBus()
        self.macro_changes: list[dict] = []
        self.bus.on("macro.state_changed", lambda p: self.macro_changes.append(p))

    def _sm(self, state=ACTIVE, last_interaction=None, dormant_since=None, **kw):
        return MacroStateMachine(
            state=state,
            last_interaction=last_interaction,
            dormant_since=dormant_since,
            event_bus=self.bus,
            **kw,
        )

    # ---- 常量与阈值 ----
    def test_state_constants(self) -> None:
        self.assertEqual((ACTIVE, IDLE, DORMANT, RETURNING), ("ACTIVE", "IDLE", "DORMANT", "RETURNING"))
        self.assertEqual(IDLE_AFTER_SECONDS, 15 * 60)
        self.assertEqual(DORMANT_AFTER_SECONDS, 7 * 24 * 3600)

    # ---- 状态机：时间驱动 ----
    def test_active_to_idle_after_15min_idle(self) -> None:
        sm = self._sm(state=ACTIVE, last_interaction=_ago(minutes=20))
        ev = sm.tick()
        self.assertEqual(sm.current_state(), IDLE)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["前态"], ACTIVE)
        self.assertEqual(ev["后态"], IDLE)
        self.assertGreater(ev["时长"], IDLE_AFTER_SECONDS)

    def test_active_stays_active_before_15min(self) -> None:
        sm = self._sm(state=ACTIVE, last_interaction=_ago(minutes=5))
        ev = sm.tick()
        self.assertEqual(sm.current_state(), ACTIVE)
        self.assertIsNone(ev)
        self.assertEqual(self.macro_changes, [])

    def test_idle_to_dormant_after_7days_no_interaction(self) -> None:
        sm = self._sm(state=IDLE, last_interaction=_ago(days=8))
        ev = sm.tick()
        self.assertEqual(sm.current_state(), DORMANT)
        self.assertIsNotNone(sm.dormant_since)
        self.assertEqual(ev["前态"], IDLE)
        self.assertEqual(ev["后态"], DORMANT)
        self.assertGreater(ev["时长"], DORMANT_AFTER_SECONDS)

    def test_dormant_stable_under_tick(self) -> None:
        # DORMANT 是稳定态：时间推进不产生任何 macro 事件
        sm = self._sm(state=DORMANT, last_interaction=_ago(days=30), dormant_since=_ago(days=29))
        for d in range(4):
            sm.tick(datetime.now() + timedelta(days=d))
        self.assertEqual(sm.current_state(), DORMANT)
        self.assertEqual(self.macro_changes, [])

    # ---- 状态机：用户主动对话驱动（决策4.3） ----
    def test_dormant_to_returning_on_user_dialogue(self) -> None:
        sm = self._sm(state=DORMANT, last_interaction=_ago(days=70), dormant_since=_ago(days=69))
        ev = sm.on_user_dialogue()
        self.assertEqual(sm.current_state(), RETURNING)
        self.assertEqual(ev["前态"], DORMANT)
        self.assertEqual(ev["后态"], RETURNING)
        self.assertEqual(self.macro_changes, [ev])

    def test_returning_to_active_on_next_user_dialogue(self) -> None:
        sm = self._sm(state=RETURNING, last_interaction=_ago(days=2))
        ev = sm.on_user_dialogue()
        self.assertEqual(sm.current_state(), ACTIVE)
        self.assertEqual(ev["后态"], ACTIVE)
        self.assertEqual(sm.last_interaction is not None, True)

    def test_idle_to_active_on_user_dialogue(self) -> None:
        sm = self._sm(state=IDLE, last_interaction=_ago(days=1))
        ev = sm.on_user_dialogue()
        self.assertEqual(sm.current_state(), ACTIVE)
        self.assertEqual(ev["前态"], IDLE)
        self.assertEqual(ev["后态"], ACTIVE)

    def test_active_refresh_no_event_on_same_state(self) -> None:
        sm = self._sm(state=ACTIVE, last_interaction=_ago(hours=1))
        ev = sm.on_user_dialogue()
        self.assertEqual(sm.current_state(), ACTIVE)
        self.assertIsNone(ev)
        self.assertEqual(self.macro_changes, [])

    # ---- 决策4.3：通知点击/后台完成不算「用户主动对话」 ----
    def test_non_dialogue_does_not_trigger_returning(self) -> None:
        sm = self._sm(state=DORMANT, last_interaction=_ago(days=30), dormant_since=_ago(days=29))
        # 通知点击（非主动对话）不改状态
        sm.on_non_dialogue_interaction()
        self.assertEqual(sm.current_state(), DORMANT)
        # 后台完成（非主动对话）不改状态
        sm.on_background_completion("已代办完成")
        self.assertEqual(sm.current_state(), DORMANT)
        # 事件列表仍为空（未触发 RETURNING）
        self.assertEqual(self.macro_changes, [])

    # ---- 三纪律①：DORMANT 主动事件总线零消息 ----
    def test_dormant_zero_proactive_messages(self) -> None:
        sm = self._sm(state=DORMANT, last_interaction=_ago(days=30), dormant_since=_ago(days=29))
        proactive: list[str] = []

        def _capture(p: dict, e: str) -> None:
            proactive.append(e)

        for name in EVENT_TYPES:
            if name.startswith("proactive."):
                self.bus.on(name, lambda p, _e=name: _capture(p, _e))
        # 推进时间多次，模拟跨天仍处 DORMANT
        for d in range(5):
            sm.tick(datetime.now() + timedelta(days=d))
        self.assertEqual(proactive, [])          # 主动事件零消息
        self.assertFalse(sm.is_proactive_allowed())  # 主动闸门关闭
        self.assertEqual(self.macro_changes, [])     # 无 macro 事件（DORMANT 稳定）

    def test_proactive_allowed_outside_dormant(self) -> None:
        for st in (ACTIVE, IDLE, RETURNING):
            self.assertTrue(self._sm(state=st).is_proactive_allowed(), st)

    # ---- 决策4.5：DORMANT 期间情感衰减暂停（M2 钩子） ----
    def test_affect_decay_paused_during_dormant(self) -> None:
        self.assertTrue(self._sm(state=DORMANT).is_affect_decay_paused())
        self.assertFalse(self._sm(state=ACTIVE).is_affect_decay_paused())
        self.assertFalse(self._sm(state=IDLE).is_affect_decay_paused())
        self.assertFalse(self._sm(state=RETURNING).is_affect_decay_paused())

    # ---- 三纪律②：零归因 ----
    def test_dormant_zero_attribution(self) -> None:
        sm = self._sm(state=DORMANT, last_interaction=_ago(days=30), dormant_since=_ago(days=29))
        sm.on_background_completion("整理了本周备忘")
        brief = sm.regression_brief()
        greeting = returning_greeting((timedelta(days=30)).total_seconds())
        for text in (brief, greeting):
            for banned in _ZERO_ATTRIBUTION_BANNED:
                self.assertNotIn(banned, text, f"零归因违约：{banned!r} in {text!r}")

    # ---- 三纪律③：托付后台任务照办但不推送 ----
    def test_background_task_runs_but_not_pushed(self) -> None:
        sm = self._sm(state=DORMANT, last_interaction=_ago(days=30), dormant_since=_ago(days=29))
        pushed: list[dict] = []
        self.bus.on("proactive.delivered", lambda p: pushed.append(p))
        ok = sm.on_background_completion("整理了本周备忘")
        self.assertTrue(ok)                       # 照办
        self.assertEqual(sm.current_state(), DORMANT)  # 状态不变
        self.assertEqual(pushed, [])              # 不推送
        self.assertIn("整理了本周备忘", sm.regression_brief())  # 只进回归简报

    # ---- 事件 payload 契约 {前态,后态,时长} ----
    def test_macro_state_changed_payload_shape(self) -> None:
        sm = self._sm(state=IDLE, last_interaction=_ago(days=20))
        ev = sm.tick()
        self.assertIn("前态", ev)
        self.assertIn("后态", ev)
        self.assertIn("时长", ev)
        self.assertEqual(set(ev.keys()), {"前态", "后态", "时长"})
        self.assertIsInstance(ev["时长"], (int, float))

    # ---- RETURNING 分层问候：三档 + 简报预算 ----
    def test_returning_greeting_tiers_and_brief_budget(self) -> None:
        # ≤3 天
        self.assertEqual(greeting_tier(timedelta(days=2).total_seconds()), "short")
        self.assertEqual(TIER_GREETING["short"], "几天没见")
        # 3 天边界仍 short
        self.assertEqual(greeting_tier(timedelta(days=3).total_seconds()), "short")
        # ≤2 周 / 中间
        self.assertEqual(greeting_tier(timedelta(days=10).total_seconds()), "mid")
        self.assertEqual(TIER_GREETING["mid"], "有一阵子了")
        self.assertEqual(TIER_TAIL["mid"], "慢慢来")
        self.assertEqual(TIER_BRIEF_MAX["mid"], 3)
        # ≥2 个月
        self.assertEqual(greeting_tier(timedelta(days=60).total_seconds()), "long")
        self.assertEqual(greeting_tier(timedelta(days=90).total_seconds()), "long")
        self.assertEqual(TIER_GREETING["long"], "好久好久不见")
        self.assertEqual(TIER_TAIL["long"], "最近有什么新变化可以告诉我的？")
        self.assertEqual(TIER_BRIEF_MAX["long"], 1)
        self.assertEqual(TIER_BRIEF_MAX["short"], 2)

    def test_returning_greeting_assembles_brief_and_tail(self) -> None:
        # long 档：问候 + 简报截断到 1 条 + 尾巴「交还主动权」
        g = returning_greeting(timedelta(days=90).total_seconds(),
                               brief_items=["A", "B", "C"])
        self.assertIn("好久好久不见", g)
        self.assertIn("最近有什么新变化可以告诉我的？", g)
        # 简报只保留前 1 条（long 预算）
        self.assertIn("A", g)
        self.assertNotIn("B", g)
        self.assertNotIn("C", g)
        # mid 档：尾巴「慢慢来」
        g2 = returning_greeting(timedelta(days=10).total_seconds(), brief_items=["a", "b", "c", "d"])
        self.assertIn("有一阵子了", g2)
        self.assertIn("慢慢来", g2)
        self.assertIn("a", g2)
        self.assertIn("c", g2)          # 前 3 条
        self.assertNotIn("d", g2)       # 第 4 条被砍

    def test_returning_greeting_never_asks_whereabout(self) -> None:
        # 永不追问去向
        for d in (2, 10, 90):
            g = returning_greeting(timedelta(days=d).total_seconds(), brief_items=["x"])
            self.assertNotIn("你去哪", g)
            self.assertNotIn("去哪了", g)
            self.assertNotIn("为什么不", g)

    def test_returning_greeting_no_brief_ok(self) -> None:
        g = returning_greeting(timedelta(days=2).total_seconds())
        self.assertIn("几天没见", g)
        self.assertEqual(g.count("\n"), 0)  # 无简报时无多余换行

    # ---- 三源简报 ----
    def test_build_returning_brief_three_sources(self) -> None:
        items = build_returning_brief(
            source_a=["系统足迹1", "系统足迹2"],
            source_b=["小二足迹1"],
            source_c=["lorebook条目1"],
        )
        self.assertEqual(items, ["系统足迹1", "系统足迹2", "小二足迹1", "lorebook条目1"])

    # ---- 持久化最后状态 ----
    def test_persist_last_state_round_trip(self) -> None:
        # 写项目内 logs/ 临时目录（参照 test_event_bus._tmpdir，规避受限环境系统 temp 权限）
        base = Path(__file__).resolve().parent.parent / "logs"
        d = base / f"ms_{uuid.uuid4().hex[:8]}"
        d.mkdir(parents=True, exist_ok=True)
        try:
            path = str(d / "macro_state.json")
            sm = self._sm(state=DORMANT, last_interaction=_ago(days=20),
                          dormant_since=_ago(days=19), persist_path=path)
            sm.save()
            sm2 = MacroStateMachine.load(persist_path=path, event_bus=self.bus)
            self.assertEqual(sm2.current_state(), DORMANT)
            self.assertEqual(sm2.last_interaction, sm.last_interaction)
            self.assertEqual(sm2.dormant_since, sm.dormant_since)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
