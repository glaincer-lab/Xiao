"""M2-C 姿态/情感事件接入总线（backend/memv2/bridge.py）单元测试。

覆盖 DoD：
1. **发布事件名均在 EVENT_TYPES 白名单**（断言 + 发布经桩验证 emit 被调用、事件名正确，
   且越界事件名 fail-fast）。
2. **订阅 ``memory.profile_updated`` 刷新画像缓存**（事件驱动刷新）。
3. **读 M1 画像走只读快照**：``get_profile_snapshot`` 返回深拷贝、改动不影响缓存；
   读缓存**不实时联查** M1（get 不调用注入的 provider）。
4. **M2 不直接 import M1 核心函数**：grep bridge.py 源码，断言无 ``memv1`` 引用。

仅标准库；发布/订阅用 ``TrackingBus`` 桩验证，不触发跨模块副作用。
MIT。
"""
from __future__ import annotations

import inspect
import unittest

from backend import event_bus
from backend.memv2 import bridge
from backend.memv2.affect import AffectState


class TrackingBus:
    """最小事件总线桩：支持 on/emit，记录所有 emit；emit 会派发给已订阅 handler。"""

    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}
        self.emitted: list[tuple[str, dict]] = []

    def on(self, name: str, handler) -> None:  # type: ignore[override]
        self.handlers.setdefault(name, []).append(handler)

        def _unsub() -> None:
            bucket = self.handlers.get(name)
            if bucket and handler in bucket:
                bucket.remove(handler)

        return _unsub

    def emit(self, name: str, payload: dict | None = None) -> None:
        data = payload if payload is not None else {}
        self.emitted.append((name, data))
        for handler in list(self.handlers.get(name, ())):
            handler(data)

    def subscribed_to(self, name: str) -> bool:
        # 与真实 EventBus 语义一致：只要有至少一个实际 handler 才算订阅着。
        return bool(self.handlers.get(name))


# 需要快照保存/恢复的模块级状态（保证用例隔离）
_STATE_ATTRS = ("_bus", "_profile_provider", "_profile_snapshot",
                "_subscription", "_subscribed_bus")


class BridgeTestCase(unittest.TestCase):
    """基类：保存并恢复 bridge 的模块级状态，保证用例彼此隔离。"""

    def setUp(self) -> None:
        self._prev = {a: getattr(bridge, a) for a in _STATE_ATTRS}

    def tearDown(self) -> None:
        for a, v in self._prev.items():
            setattr(bridge, a, v)


# --------------------------------------------------------------------------- #
# 事件名白名单 + 发布
# --------------------------------------------------------------------------- #
class EventNameWhitelistTest(BridgeTestCase):
    """DoD：发布事件名均在 EVENT_TYPES 白名单。"""

    def test_bridge_event_constants_in_whitelist(self) -> None:
        for name in (bridge.EVENT_POSTURE_CHANGED,
                     bridge.EVENT_AFFECT_UPDATED,
                     bridge.EVENT_POSTURE_DECISION,
                     bridge.EVENT_PROFILE_UPDATED):
            with self.subTest(name=name):
                self.assertIn(name, event_bus.EVENT_TYPES)

    def test_guard_rejects_non_whitelist_event(self) -> None:
        with self.assertRaises(ValueError):
            bridge._ensure_event("posture.not_listed")
        with self.assertRaises(ValueError):
            bridge._ensure_event("")          # 空名
        with self.assertRaises(ValueError):
            bridge._ensure_event("shadow.nope")


class PublishEventTest(BridgeTestCase):
    """DoD：发布经桩验证（bus.emit 被调用、事件名正确、payload 与契约一致）。"""

    def setUp(self) -> None:
        super().setUp()
        self.bus = TrackingBus()
        bridge._bus = self.bus

    def test_publish_posture_change(self) -> None:
        self.assertIsNone(
            bridge.publish_posture_change("friend", "companion", {"emotion_word": 1})
        )
        self.assertEqual(len(self.bus.emitted), 1)
        name, payload = self.bus.emitted[0]
        self.assertEqual(name, bridge.EVENT_POSTURE_CHANGED)
        self.assertEqual(payload["previous"], "friend")
        self.assertEqual(payload["current"], "companion")
        self.assertEqual(payload["signal"], {"emotion_word": 1})

    def test_publish_affect_updated(self) -> None:
        state = AffectState(mood=52, intimacy=21, last_interaction=None)
        self.assertIsNone(bridge.publish_affect_updated(state, "praise"))
        self.assertEqual(len(self.bus.emitted), 1)
        name, payload = self.bus.emitted[0]
        self.assertEqual(name, bridge.EVENT_AFFECT_UPDATED)
        self.assertEqual(payload["mood"], 52)
        self.assertEqual(payload["intimacy"], 21)
        self.assertEqual(payload["reason"], "praise")

    def test_publish_affect_updated_accepts_dict_state(self) -> None:
        bridge.publish_affect_updated({"mood": 49, "intimacy": 20}, "scold")
        self.assertEqual(self.bus.emitted[0][0], bridge.EVENT_AFFECT_UPDATED)
        self.assertEqual(self.bus.emitted[0][1]["mood"], 49)

    def test_log_shadow_decision(self) -> None:
        bridge.log_shadow_decision("s-01", "companion", {"late_night": 1}, 0.72)
        self.assertEqual(len(self.bus.emitted), 1)
        name, payload = self.bus.emitted[0]
        self.assertEqual(name, bridge.EVENT_POSTURE_DECISION)
        # 事件契约为 {会话id,决策,信号}；score 只入影子记录，不入事件载荷。
        self.assertEqual(payload["session_id"], "s-01")
        self.assertEqual(payload["decision"], "companion")
        self.assertEqual(payload["signals"], {"late_night": 1})
        self.assertNotIn("score", payload)

    def test_signal_copied_not_mutated(self) -> None:
        sig = {"emotion_word": 1}
        bridge.publish_posture_change("friend", "companion", sig)
        sig["emotion_word"] = 999  # 事后改输入，不应污染已发布事件
        self.assertEqual(self.bus.emitted[0][1]["signal"], {"emotion_word": 1})


# --------------------------------------------------------------------------- #
# M1 画像只读快照（缓存 + 事件刷新，不实时联查）
# --------------------------------------------------------------------------- #
class ProfileSnapshotTest(BridgeTestCase):
    """DoD：读 M1 画像走只读快照（不直调 M1 内部；读缓存不实时联查）。"""

    def test_set_provider_populates_snapshot(self) -> None:
        bridge.reset_profile_provider()
        bridge.set_profile_provider(lambda: {"version": "v1", "intimacy": 25})
        self.assertEqual(bridge.get_profile_snapshot(),
                         {"version": "v1", "intimacy": 25})

    def test_reset_profile_provider_clears(self) -> None:
        bridge.set_profile_provider(lambda: {"version": "v1"})
        bridge.reset_profile_provider()
        self.assertEqual(bridge.get_profile_snapshot(), {})

    def test_get_snapshot_is_read_only_copy(self) -> None:
        bridge.set_profile_provider(lambda: {"version": "v1", "nested": {"a": 1}})
        snap = bridge.get_profile_snapshot()
        snap["version"] = "hacked"          # 改形参返回的拷贝
        snap["nested"]["a"] = 999           # 改嵌套结构
        self.assertEqual(bridge.get_profile_snapshot()["version"], "v1")
        self.assertEqual(bridge.get_profile_snapshot()["nested"]["a"], 1)

    def test_get_snapshot_does_not_query_m1(self) -> None:
        calls: list[int] = []
        bridge.reset_profile_provider()
        bridge.set_profile_provider(lambda: (calls.append(1), {"version": "v1"})[1])
        calls.clear()                        # 清除 set 时那次 refresh 的调用
        bridge.get_profile_snapshot()
        self.assertEqual(calls, [], "get_profile_snapshot 读缓存，不得再调 provider")

    def test_refresh_re_reads_provider(self) -> None:
        current = {"version": "v1"}
        bridge.set_profile_provider(lambda: dict(current))
        current["version"] = "v2"
        bridge.refresh_profile()
        self.assertEqual(bridge.get_profile_snapshot()["version"], "v2")


class SubscribeRefreshTest(BridgeTestCase):
    """DoD：订阅 memory.profile_updated → 画像缓存刷新。"""

    def test_subscribe_rebinds_and_refreshes_on_event(self) -> None:
        bus = TrackingBus()
        bridge._bus = bus
        bridge._subscription = None
        bridge._subscribed_bus = None
        current = {"version": "v1"}
        bridge.set_profile_provider(lambda: dict(current))

        unsub = bridge.init()
        self.assertTrue(bus.subscribed_to(bridge.EVENT_PROFILE_UPDATED))
        self.assertIsNotNone(unsub)

        # 来源变化后发出 memory.profile_updated → 缓存刷新为新版本。
        current["version"] = "v2"
        bus.emit(bridge.EVENT_PROFILE_UPDATED,
                 {"version": "v2", "changed": ["intimacy"]})
        self.assertEqual(bridge.get_profile_snapshot()["version"], "v2")

    def test_init_is_idempotent_same_bus(self) -> None:
        bus = TrackingBus()
        bridge._bus = bus
        bridge._subscription = None
        bridge._subscribed_bus = None
        bridge.set_profile_provider(lambda: {"version": "v1"})
        bridge.init()
        first = len(bus.handlers[bridge.EVENT_PROFILE_UPDATED])
        bridge.init()
        second = len(bus.handlers[bridge.EVENT_PROFILE_UPDATED])
        self.assertEqual(first, 1)
        self.assertEqual(second, 1, "同一总线重复 init 不应叠加订阅")

    def test_shutdown_unsubscribes(self) -> None:
        bus = TrackingBus()
        bridge._bus = bus
        bridge._subscription = None
        bridge._subscribed_bus = None
        bridge.set_profile_provider(lambda: {"version": "v1"})
        bridge.init()
        bridge.shutdown()
        self.assertFalse(bus.subscribed_to(bridge.EVENT_PROFILE_UPDATED))


# --------------------------------------------------------------------------- #
# M2 不直接 import M1 核心函数（grep 断言的 DoD）
# --------------------------------------------------------------------------- #
class NoMemv1ImportTest(unittest.TestCase):
    """DoD：M2 不直接 import M1 核心函数（读 M1 走注入 provider，不经 memv1 模块）。"""

    def test_bridge_source_has_no_memv1_reference(self) -> None:
        src = inspect.getsource(bridge)
        for forbidden in ("import backend.memv1", "from backend.memv1",
                          "backend.memv1 import", "memv1."):
            self.assertNotIn(forbidden, src,
                             f"bridge.py 不应引用 memv1：{forbidden!r}")


if __name__ == "__main__":
    unittest.main()
