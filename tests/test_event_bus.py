"""跨模块事件总线（backend/event_bus.py）单元测试（M0）。

覆盖：按类型订阅/发布、取消订阅、payload 缺省、未知事件名 fail-fast、
单订阅者异常不影响其它订阅者、EVENT_TYPES 单一来源完整性。
"""
from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
