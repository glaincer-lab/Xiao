"""M3-M3 同类窗口聚合（防事件风暴）单元测试。

TDD 契约：先写验收断言再实现，覆盖 docs/specs/M3-proactive.md：
    §4.3 事件触发 —— 同类窗口聚合（防事件风暴）
    §7 验收断言 —— 同类窗口聚合（防事件风暴）

验收断言（见各 TestCase）：
    1. 默认窗口为 30 分钟（1800 秒）
    2. 30 分钟窗口内多个同一类事件 → 只触发一次（首次 fired，其余 suppressed）
    3. 窗口外的新同类事件可再触发（fired）
    4. 不同 key（不同类事件）互不抑制
    5. 聚合日志记录 fired/suppressed（供「防风暴」断言）
    6. last_fired 跟踪最近触发时间戳；reset 清空状态

运行：python -m unittest tests.test_m3_aggregate -v
"""
from __future__ import annotations

import unittest

from backend.m3.aggregate import DEFAULT_WINDOW_SECONDS, EventWindowAggregator

# 固定 epoch 基准，便于构造窗口内/外时刻
_BASE = 1_700_000_000.0


def _ts(seconds: float) -> float:
    return _BASE + seconds


class TestEventWindowAggregator(unittest.TestCase):
    def test_default_window_is_30_minutes(self) -> None:
        # 30 分钟同类窗口（§4.3 防事件风暴）
        self.assertEqual(DEFAULT_WINDOW_SECONDS, 1800)

    def test_same_key_within_window_triggers_once(self) -> None:
        # 同一类事件在窗口内：首次触发、其后抑制（只触发一次）
        agg = EventWindowAggregator(window_seconds=1800, now_fn=lambda: _ts(0))
        self.assertTrue(agg.should_trigger("env.anomaly", _ts(0)))
        self.assertFalse(agg.should_trigger("env.anomaly", _ts(600)))    # 窗口内 → 抑制
        self.assertFalse(agg.should_trigger("env.anomaly", _ts(1799)))   # 窗口内边缘 → 抑制

    def test_after_window_triggers_again(self) -> None:
        # 窗口外的新同类事件可再触发
        agg = EventWindowAggregator(window_seconds=1800, now_fn=lambda: _ts(0))
        self.assertTrue(agg.should_trigger("env.anomaly", _ts(0)))
        self.assertFalse(agg.should_trigger("env.anomaly", _ts(900)))
        self.assertTrue(agg.should_trigger("env.anomaly", _ts(1801)))    # 窗口外 → 再触发

    def test_different_key_not_suppressed(self) -> None:
        # 不同类（不同 key）事件互不抑制
        agg = EventWindowAggregator(window_seconds=1800, now_fn=lambda: _ts(0))
        self.assertTrue(agg.should_trigger("env.anomaly", _ts(0)))
        self.assertTrue(agg.should_trigger("device.state_changed", _ts(10)))

    def test_log_records_fired_and_suppressed(self) -> None:
        # 聚合日志：首次 fired、其后 suppressed（供「防风暴」断言）
        agg = EventWindowAggregator(window_seconds=1800, now_fn=lambda: _ts(0))
        agg.should_trigger("a", _ts(0))
        agg.should_trigger("a", _ts(1))
        act = [e["action"] for e in agg.log]
        self.assertEqual(act, ["fired", "suppressed"])

    def test_last_fired_tracks_ts(self) -> None:
        agg = EventWindowAggregator(window_seconds=1800, now_fn=lambda: _ts(0))
        agg.should_trigger("a", _ts(5))
        self.assertEqual(agg.last_fired("a"), _ts(5))
        self.assertIsNone(agg.last_fired("b"))

    def test_reset_clears_state(self) -> None:
        agg = EventWindowAggregator(window_seconds=1800, now_fn=lambda: _ts(0))
        agg.should_trigger("a", _ts(0))
        agg.reset()
        self.assertIsNone(agg.last_fired("a"))
        self.assertEqual(agg.log, [])

    def test_now_fn_used_when_now_omitted(self) -> None:
        # 未显式传 now 时，使用注入的 now_fn
        agg = EventWindowAggregator(window_seconds=1800, now_fn=lambda: _ts(1000))
        self.assertTrue(agg.should_trigger("a"))
        self.assertEqual(agg.last_fired("a"), _ts(1000))


if __name__ == "__main__":
    unittest.main()
