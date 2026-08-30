"""跨模块事件总线（backend/event_bus.py）单元测试（M0）。

覆盖：按类型订阅/发布、取消订阅、payload 缺省、未知事件名 fail-fast、
单订阅者异常不影响其它订阅者、EVENT_TYPES 单一来源完整性。
"""
from __future__ import annotations

import contextlib
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

_TMP_BASE = Path(__file__).resolve().parent.parent / "logs"


@contextlib.contextmanager
def _tmpdir():
    d = _TMP_BASE / f"t_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    try:
        yield str(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


from backend.event_bus import EVENT_TYPES, EventBus, bus


class EventBusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()

    def test_subscribe_and_emit_receives_payload(self) -> None:
        seen: list[dict] = []
        self.bus.on("memory.profile_updated", lambda p: seen.append(p))
        self.bus.emit("memory.profile_updated", {"version": "v1", "changed": ["content"]})
        self.assertEqual(seen, [{"version": "v1", "changed": ["content"]}])

    def test_unsubscribe_stops_delivery(self) -> None:
        seen: list[dict] = []
        unsub = self.bus.on("posture.changed", lambda p: seen.append(p))
        unsub()
        self.bus.emit("posture.changed", {"前态": "friend", "后态": "steward"})
        self.assertEqual(seen, [])
        self.assertEqual(self.bus.count("posture.changed"), 0)

    def test_emit_without_payload_defaults_to_empty_dict(self) -> None:
        seen: list[dict] = []
        self.bus.on("gateway.blocked", lambda p: seen.append(p))
        self.bus.emit("gateway.blocked")
        self.assertEqual(seen, [{}])

    def test_unknown_event_type_fails_fast(self) -> None:
        with self.assertRaises(ValueError):
            self.bus.on("not.a.real.event", lambda p: None)
        with self.assertRaises(ValueError):
            self.bus.emit("not.a.real.event", {})

    def test_handler_exception_does_not_affect_others(self) -> None:
        seen: list[str] = []

        def boom(p: dict) -> None:
            raise RuntimeError("boom")

        self.bus.on("growth.canonized", boom)
        self.bus.on("growth.canonized", lambda p: seen.append("ok"))
        self.bus.emit("growth.canonized", {"记录id": "x"})
        self.assertEqual(seen, ["ok"])

    def test_module_bus_singleton_accepts_registered_types(self) -> None:
        # 默认单例能正常收发；不抛异常即可。
        seen = []
        unsub = bus.on("macro.state_changed", lambda p: seen.append(p))
        try:
            bus.emit("macro.state_changed", {"前态": "ACTIVE", "后态": "IDLE"})
            self.assertEqual(len(seen), 1)
        finally:
            unsub()

    def test_event_types_cover_registry_contract(self) -> None:
        # 白名单必须包含 EVENT_REGISTRY §一 的跨模块语义化事件名（抽验关键事件）。
        for name in (
            "macro.state_changed", "memory.profile_updated", "memory.clarify_request",
            "posture.changed", "affect.updated", "proactive.delivered", "vision.conclusion",
            "device.state_changed", "growth.canonized", "gateway.entities_found", "user.feedback",
        ):
            self.assertIn(name, EVENT_TYPES)


    def test_persist_and_replay_recovers_after_restart(self) -> None:
        """持久化开启时：新实例从同一日志目录重放，崩后不丢未溢出事件。"""

        with _tmpdir() as d:
            bus1 = EventBus(persist=True, log_dir=d)
            bus1.on("memory.profile_updated", lambda p: None)
            bus1.emit("memory.profile_updated", {"version": "v1"})
            bus1.emit("posture.changed", {"前态": "friend", "后态": "steward"})

            # 模拟进程重启：从同一日志目录重新加载，replay 应恢复事件
            bus2 = EventBus(persist=True, log_dir=d)
            recs = bus2.replay()
            self.assertEqual(len(recs), 2)
            self.assertEqual(recs[0]["event"], "memory.profile_updated")
            self.assertEqual(recs[0]["payload"], {"version": "v1"})

            # 重放后仍可正常订阅/发布（事件契约未被破坏）
            seen = []
            bus2.on("memory.profile_updated", lambda p: seen.append(p))
            bus2.emit("memory.profile_updated", {"version": "v2"})
            self.assertEqual(seen, [{"version": "v2"}])

    def test_queue_overflow_drops_oldest_and_logs(self) -> None:
        """队列超上限：丢弃最旧事件并记录告警日志（止血：有界队列）。"""

        with _tmpdir() as d:
            bus = EventBus(persist=True, max_events=2, log_dir=d)
            bus.emit("growth.canonized", {"记录id": "a"})
            bus.emit("growth.canonized", {"记录id": "b"})
            with self.assertLogs("backend.event_bus", level="WARNING") as cm:
                bus.emit("growth.canonized", {"记录id": "c"})
            self.assertEqual(bus.persisted_count(), 2)
            recs = bus.replay("growth.canonized")
            self.assertEqual([r["payload"]["记录id"] for r in recs], ["b", "c"])
            self.assertTrue(any("溢出" in line for line in cm.output))
            # 落盘校验：重载实例只保留最近 max_events 条
            bus3 = EventBus(persist=True, max_events=2, log_dir=d)
            self.assertEqual(bus3.persisted_count(), 2)
            self.assertEqual(
                [r["payload"]["记录id"] for r in bus3.replay("growth.canonized")],
                ["b", "c"],
            )

    def test_persistence_off_by_default_no_log(self) -> None:
        """持久化默认关闭：emit 不落盘（不改变默认行为）。"""
        from pathlib import Path

        with _tmpdir() as d:
            bus = EventBus(log_dir=d)  # persist 默认 False
            bus.emit("gateway.blocked", {"词类": "x", "处置": "block"})
            self.assertEqual(bus.persisted_count(), 0)
            self.assertFalse((Path(d) / "events.jsonl").exists())

    def test_persist_keeps_exception_isolation(self) -> None:
        """持久化开启时，单订阅者异常仍不影响其它订阅者（A 的隔离不被 B 破坏）。"""

        with _tmpdir() as d:
            bus = EventBus(persist=True, log_dir=d)
            seen = []

            def boom(p: dict) -> None:
                raise RuntimeError("boom")

            bus.on("growth.canonized", boom)
            bus.on("growth.canonized", lambda p: seen.append("ok"))
            bus.emit("growth.canonized", {"记录id": "x"})
            self.assertEqual(seen, ["ok"])

if __name__ == "__main__":
    unittest.main()