"""M2-B 情感状态机（backend/memv2/affect.py）单元测试。

覆盖 DoD：
1. mood / intimacy 范围钳制（0-100，回归下限 >0 不归零）；
2. 事件驱动断言（夸赞 +2 / 骂 -3）；
3. 衰减回归断言（长时间无互动缓慢衰减，有下限不归零）；
4. ``get_visual_state()`` 只含形态键、不含任意 mood / intimacy 数值（红线，测试锁死）；
5. 线程安全（并发事件驱动下状态原子、无丢更新）。

仅标准库；自包含，无 LLM；不硬 import 任何未落地模块（本模块只依赖自身与标准库）。
"""
from __future__ import annotations

import threading
import unittest

from backend.memv2 import affect


class ColdStartTest(unittest.TestCase):
    """DoD：冷启动默认值（契约写死 mood=50 / intimacy=20）。"""

    def setUp(self) -> None:
        affect.reset()

    def test_default_values(self) -> None:
        st = affect.get_state()
        self.assertEqual(st.mood, affect.MOOD_DEFAULT)
        self.assertEqual(st.intimacy, affect.INTIMACY_DEFAULT)
        self.assertIsNone(st.last_interaction)

    def test_range_bounds_are_positive_and_ordered(self) -> None:
        # 回归下限必须 >0（不归零），且上界为 100
        self.assertGreater(affect.MOOD_MIN, 0)
        self.assertGreater(affect.INTIMACY_MIN, 0)
        self.assertEqual(affect.MOOD_MAX, 100)
        self.assertEqual(affect.INTIMACY_MAX, 100)
        self.assertLess(affect.MOOD_MIN, affect.MOOD_MAX)
        self.assertLess(affect.INTIMACY_MIN, affect.INTIMACY_MAX)

    def test_reset_returns_fresh_state(self) -> None:
        st = affect.reset()
        self.assertEqual(st.mood, affect.MOOD_DEFAULT)


class EventDrivenTest(unittest.TestCase):
    """DoD：事件驱动断言（夸赞 +2 / 骂 -3）。"""

    def setUp(self) -> None:
        affect.reset()

    def test_praise_adds_two_to_mood(self) -> None:
        st = affect.apply_event(affect.EVENT_PRAISE)
        self.assertEqual(st.mood, affect.MOOD_DEFAULT + 2)

    def test_scold_subtracts_three_from_mood(self) -> None:
        st = affect.apply_event(affect.EVENT_SCOLD)
        self.assertEqual(st.mood, affect.MOOD_DEFAULT - 3)

    def test_late_night_talk_raises_mood_and_intimacy(self) -> None:
        st = affect.apply_event(affect.EVENT_LATE_NIGHT_TALK)
        self.assertEqual(st.mood, affect.MOOD_DEFAULT + 1)
        self.assertEqual(st.intimacy, affect.INTIMACY_DEFAULT + 1)

    def test_no_interaction_3d_pulls_both_down_slowly(self) -> None:
        st = affect.apply_event(affect.EVENT_NO_INTERACTION_3D)
        self.assertEqual(st.mood, affect.MOOD_DEFAULT - 1)
        self.assertEqual(st.intimacy, affect.INTIMACY_DEFAULT - 1)

    def test_event_updates_last_interaction(self) -> None:
        st = affect.apply_event(affect.EVENT_PRAISE)
        self.assertIsNotNone(st.last_interaction)

    def test_unknown_event_is_idempotent(self) -> None:
        before = affect.get_state()
        st = affect.apply_event("完全未知事件")
        self.assertEqual(st.mood, before.mood)
        self.assertEqual(st.intimacy, before.intimacy)
        self.assertEqual(st.last_interaction, before.last_interaction)

    def test_empty_and_none_event_are_idempotent(self) -> None:
        before = affect.get_state()
        self.assertEqual(affect.apply_event("").mood, before.mood)
        self.assertEqual(affect.apply_event(None).mood, before.mood)  # type: ignore[arg-type]


class RangeClampTest(unittest.TestCase):
    """DoD：mood/intimacy 钳制到 [MIN, MAX]，超出即封顶 / 触底。"""

    def setUp(self) -> None:
        affect.reset()

    def test_repeated_praise_caps_at_max(self) -> None:
        for _ in range(100):
            affect.apply_event(affect.EVENT_PRAISE)
        st = affect.get_state()
        self.assertEqual(st.mood, affect.MOOD_MAX)
        self.assertLessEqual(st.mood, 100)

    def test_repeated_scold_floors_below_not_zero(self) -> None:
        for _ in range(100):
            affect.apply_event(affect.EVENT_SCOLD)
        st = affect.get_state()
        # 触底但绝不归零：等于回归下限
        self.assertEqual(st.mood, affect.MOOD_MIN)
        self.assertGreater(st.mood, 0)

    def test_repeated_late_night_talk_caps_intimacy(self) -> None:
        for _ in range(300):
            affect.apply_event(affect.EVENT_LATE_NIGHT_TALK)
        st = affect.get_state()
        self.assertEqual(st.intimacy, affect.INTIMACY_MAX)
        self.assertEqual(st.mood, affect.MOOD_MAX)  # mood 同步封顶


class DecayRegressionTest(unittest.TestCase):
    """DoD：衰减回归——长时间无互动缓慢衰减，有下限不归零。"""

    def setUp(self) -> None:
        affect.reset()

    def test_decay_reduces_values_monotonically(self) -> None:
        before = affect.get_state()
        st = affect.decay(10)
        self.assertLess(st.mood, before.mood)
        self.assertLess(st.intimacy, before.intimacy)

    def test_decay_zero_days_is_noop(self) -> None:
        affect.reset()
        st = affect.decay(0)
        self.assertEqual(st.mood, affect.MOOD_DEFAULT)
        self.assertEqual(st.intimacy, affect.INTIMACY_DEFAULT)

    def test_decay_negative_days_is_noop(self) -> None:
        affect.reset()
        st = affect.decay(-5)
        self.assertEqual(st.mood, affect.MOOD_DEFAULT)

    def test_long_decay_never_reaches_zero(self) -> None:
        # 1000 天无互动 → 缓慢衰减但有回归下限，绝不为 0
        st = affect.decay(1000)
        self.assertGreater(st.mood, 0)
        self.assertGreater(st.intimacy, 0)
        self.assertEqual(st.mood, affect.MOOD_MIN)  # 触底即回归下限
        self.assertEqual(st.intimacy, affect.INTIMACY_MIN)

    def test_decay_preserves_last_interaction(self) -> None:
        affect.apply_event(affect.EVENT_PRAISE)
        before = affect.get_state()
        st = affect.decay(30)
        self.assertEqual(st.last_interaction, before.last_interaction)  # 无互动不改时间戳

    def test_praise_after_decay_recovers(self) -> None:
        # 回归机制：衰减到低位后，一次正向事件即回升（不永远卡在低点）
        affect.decay(1000)
        before = affect.get_state()
        st = affect.apply_event(affect.EVENT_PRAISE)
        self.assertEqual(st.mood, before.mood + 2)
        self.assertGreater(st.mood, affect.MOOD_MIN)


class VisualOnlyTest(unittest.TestCase):
    """DoD：只显形态——不暴露任何 mood/intimacy 数值（红线）。"""

    def setUp(self) -> None:
        affect.reset()

    def test_visual_keys_are_only_morphology(self) -> None:
        v = affect.get_visual_state()
        self.assertEqual(set(v.keys()), {"hue", "brightness", "flow_speed"})

    def test_visual_never_contains_mood_or_intimacy_keys(self) -> None:
        for _ in range(5):  # 多种状态抽样
            v = affect.get_visual_state()
            self.assertNotIn("mood", v)
            self.assertNotIn("intimacy", v)
            self.assertFalse(any(k in v for k in ("mood", "intimacy")))
            affect.apply_event(affect.EVENT_PRAISE)

    def test_visual_values_are_numeric_and_bounded(self) -> None:
        for _ in range(3):
            v = affect.get_visual_state()
            for key in ("hue", "brightness", "flow_speed"):
                val = v[key]
                self.assertIsInstance(val, (int, float))
                self.assertGreaterEqual(val, 0)
                self.assertLessEqual(val, 360 if key == "hue" else 1.0)
            affect.apply_event(affect.EVENT_PRAISE)

    def test_brightness_rises_with_higher_mood(self) -> None:
        # 压到低位 → 亮度低
        for _ in range(100):
            affect.apply_event(affect.EVENT_SCOLD)
        low = affect.get_visual_state()["brightness"]
        # 拉回高位 → 亮度高
        for _ in range(200):
            affect.apply_event(affect.EVENT_PRAISE)
        high = affect.get_visual_state()["brightness"]
        self.assertGreater(high, low)


class ThreadSafetyTest(unittest.TestCase):
    """DoD：线程安全——并发事件驱动不丢更新、状态始终合法。"""

    def setUp(self) -> None:
        affect.reset()

    def test_concurrent_praise_all_hits_accumulate(self) -> None:
        # 8 线程 × 3 次夸赞 = 24 次 × (+2) = +48 → mood = 50 + 48 = 98（未封顶）
        n_threads, per_thread = 8, 3

        def worker() -> None:
            for _ in range(per_thread):
                affect.apply_event(affect.EVENT_PRAISE)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        st = affect.get_state()
        self.assertEqual(st.mood, affect.MOOD_DEFAULT + n_threads * per_thread * 2)
        self.assertEqual(st.intimacy, affect.INTIMACY_DEFAULT)

    def test_concurrent_mixed_events_keep_state_in_bounds(self) -> None:
        def worker() -> None:
            for _ in range(50):
                affect.apply_event(affect.EVENT_PRAISE)
                affect.apply_event(affect.EVENT_SCOLD)
                affect.apply_event(affect.EVENT_LATE_NIGHT_TALK)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        st = affect.get_state()
        self.assertGreaterEqual(st.mood, affect.MOOD_MIN)
        self.assertLessEqual(st.mood, affect.MOOD_MAX)
        self.assertGreaterEqual(st.intimacy, affect.INTIMACY_MIN)
        self.assertLessEqual(st.intimacy, affect.INTIMACY_MAX)


if __name__ == "__main__":
    unittest.main()
