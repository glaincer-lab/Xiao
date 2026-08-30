"""M2-D 影子日志（backend/memv2/shadow.py）单元测试。

覆盖 DoD 核心断言——**只记录不切换（无姿态副作用）**，外加字段契约与事件广播：
1. record 追加一条记录（会话 id / 触发信号 / 组合得分 / 判定结果 / 时间戳），返回 None。
2. get_entries 返回拷贝、count 递增、条目字段完整。
3. 无姿态副作用：注入 posture 控制器 spy，多次 record 后从未被调用、
   当前姿态未变（断言锁死——若发生任何切换，守卫会抛 AssertionError）。
4. 可选注入 bus 时广播 shadow.posture_decision（payload 与事件契约一致）。
5. signals 以拷贝存储、max_entries 保留最近 N 条。

仅标准库；MIT。
"""
from __future__ import annotations

import unittest

from backend.memv2 import shadow as sh


class FakeBus:
    """最小事件总线桩：记录所有 emit 调用。"""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict]] = []

    def emit(self, name: str, payload: dict) -> None:
        self.emitted.append((name, payload))


class PostureSpy:
    """姿态控制器 spy：任何对它的调用都被记录（且绝不发生在 record 中）。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.current = "friend"

    def switch(self, new_posture: str) -> None:
        self.calls.append(("switch", new_posture))
        self.current = new_posture

    def exit(self) -> None:
        self.calls.append(("exit",))


class RecordTest(unittest.TestCase):
    def test_record_returns_none_and_appends(self) -> None:
        log = sh.ShadowLog()
        self.assertIsNone(log.record("s-001", "friend", {"emotion_word": 1}, 0.72))
        self.assertEqual(log.count(), 1)
        self.assertEqual(len(log.get_entries()), 1)

    def test_entry_has_all_contract_fields(self) -> None:
        log = sh.ShadowLog()
        log.record("s-002", "companion", {"late_night": 1, "emotion_word": 0.5}, 0.61)
        e = log.get_entries()[0]
        for key in ("session_id", "decision", "signals", "score", "timestamp"):
            self.assertIn(key, e)
        self.assertEqual(e["session_id"], "s-002")
        self.assertEqual(e["decision"], "companion")
        self.assertEqual(e["signals"], {"late_night": 1, "emotion_word": 0.5})
        self.assertEqual(e["score"], 0.61)
        self.assertIsInstance(e["timestamp"], str)
        self.assertTrue(e["timestamp"])


class NoPostureSideEffectTest(unittest.TestCase):
    def test_record_never_switches_posture(self) -> None:
        spy = PostureSpy()
        log = sh.ShadowLog(posture_controller=spy)
        for i in range(20):
            log.record(f"s-{i}", "friend", {"emotion_word": i % 2}, i / 100)
        # 姿态控制器从未被调用，当前姿态未变——只记录，不切换。
        self.assertEqual(spy.calls, [])
        self.assertEqual(spy.current, "friend")
        self.assertEqual(log._posture_controller_hits, 0)

    def test_guard_raises_if_any_switch_attempted(self) -> None:
        """守卫锁死：一旦检测到任何姿态切换迹象，record 立即抛 AssertionError。"""
        log = sh.ShadowLog(posture_controller=object())
        # 模拟外部 spy 汇报“姿态控制器被调用过”（即发生了切换迹象）
        log._mark_posture_controller_hit()
        with self.assertRaises(AssertionError):
            log.record("s-x", "friend", {}, 0.5)


class BusEmissionTest(unittest.TestCase):
    def test_record_emits_shadow_event(self) -> None:
        bus = FakeBus()
        log = sh.ShadowLog(bus=bus)
        log.record("s-003", "emergency", {"emergency_word": 1}, 0.9)
        self.assertEqual(len(bus.emitted), 1)
        name, payload = bus.emitted[0]
        self.assertEqual(name, sh.EVENT_POSTURE_DECISION)
        # 事件契约 payload: {会话id, 决策, 信号}; 得分仅入影子记录，不入事件。
        self.assertEqual(payload["session_id"], "s-003")
        self.assertEqual(payload["decision"], "emergency")
        self.assertEqual(payload["signals"], {"emergency_word": 1})

    def test_record_without_bus_does_not_emit(self) -> None:
        log = sh.ShadowLog()
        log.record("s-004", "friend", {}, 0.4)
        self.assertEqual(log._bus, None)
        self.assertEqual(log.count(), 1)


class StorageSemanticsTest(unittest.TestCase):
    def test_get_entries_returns_copy(self) -> None:
        log = sh.ShadowLog()
        log.record("s-005", "friend", {"a": 1}, 0.3)
        entries = log.get_entries()
        entries.clear()
        self.assertEqual(log.count(), 1, "get_entries 应返回拷贝，改外部列表不影响内部")

    def test_signals_stored_as_copy(self) -> None:
        log = sh.ShadowLog()
        sig = {"emotion_word": 1}
        log.record("s-006", "friend", sig, 0.2)
        sig["emotion_word"] = 999  # 事后改输入，不应污染已存记录
        self.assertEqual(log.get_entries()[0]["signals"], {"emotion_word": 1})

    def test_max_entries_keeps_recent(self) -> None:
        log = sh.ShadowLog(max_entries=3)
        for i in range(5):
            log.record(f"s-{i}", "friend", {"i": i}, i / 10)
        self.assertEqual(log.count(), 3)
        self.assertEqual(log.get_entries()[0]["session_id"], "s-2")
        self.assertEqual(log.get_entries()[-1]["session_id"], "s-4")


if __name__ == "__main__":
    unittest.main()
