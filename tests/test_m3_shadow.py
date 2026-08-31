"""M3-M6 影子期假投递验收测试。"""
import unittest

from backend.m3.budget import ProactiveBudget
from backend.m3.notify import ProactiveNotifier, SHADOW_RECORDED, DELIVERED
from backend.m3.shadow import ShadowRecorder


class _Sensor:
    def is_fullscreen(self): return False
    def is_idle(self): return False


class _Macro:
    def is_proactive_allowed(self): return True


class _Bus:
    def __init__(self): self.events = []
    def emit(self, event_type, payload): self.events.append((event_type, payload))
    def count(self, event_type): return sum(1 for e, _ in self.events if e == event_type)


def _candidate():
    return {"类型": "test", "内容草案": "草案", "特征": {"urgency": 0.9, "actionability": 0.8, "relationship": 0.5, "freshness": 0.6}}


class TestShadowNoRealDelivery(unittest.TestCase):
    def test_shadow_no_delivered_event(self):
        bus = _Bus()
        rec = ShadowRecorder()
        n = ProactiveNotifier(budget=ProactiveBudget(daily_quota=10), sensor=_Sensor(), macro=_Macro(), bus=bus, shadow=True, shadow_recorder=rec)
        status = n.process(_candidate())
        self.assertEqual(status, SHADOW_RECORDED)
        self.assertEqual(bus.count("proactive.delivered"), 0)
        self.assertGreaterEqual(bus.count("proactive.candidate"), 1)

    def test_shadow_records(self):
        bus = _Bus()
        rec = ShadowRecorder()
        n = ProactiveNotifier(budget=ProactiveBudget(daily_quota=10), sensor=_Sensor(), macro=_Macro(), bus=bus, shadow=True, shadow_recorder=rec)
        n.process(_candidate())
        self.assertEqual(len(rec.records), 1)
        self.assertTrue(rec.records[0]["是否达阈"])
        self.assertEqual(rec.records[0]["将消费"], 1)

    def test_real_mode_delivers(self):
        bus = _Bus()
        n = ProactiveNotifier(budget=ProactiveBudget(daily_quota=10), sensor=_Sensor(), macro=_Macro(), bus=bus, shadow=False)
        status = n.process(_candidate())
        self.assertEqual(status, DELIVERED)
        self.assertEqual(bus.count("proactive.delivered"), 1)


class TestShadowResponseRate(unittest.TestCase):
    def test_response_rate_collection(self):
        rec = ShadowRecorder(now_fn=lambda: 1000.0)
        rec.record({"id": "a"})
        rec.record({"id": "b"})
        rec.note_response()
        self.assertEqual(rec.response_rate(), round(1 / 2, 3))

    def test_empty(self):
        rec = ShadowRecorder()
        self.assertEqual(rec.response_rate(), 0.0)


if __name__ == "__main__":
    unittest.main()
