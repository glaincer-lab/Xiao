"""M5-M3 场景编排验收测试。"""
import unittest

from backend.m5.scene import SceneOrchestrator


class _HA:
    def __init__(self, ok=True):
        self.ok = ok
        self.scene_calls = []

    def call_scene(self, name):
        self.scene_calls.append(name)
        return self.ok


class _Bus:
    def __init__(self):
        self.events = []

    def emit(self, t, p):
        self.events.append((t, p))


class TestSceneAtomicCall(unittest.TestCase):
    def test_single_scene_call(self):
        ha = _HA(True)
        orch = SceneOrchestrator(ha_client=ha, bus=_Bus())
        orch.trigger_scene("movie")
        self.assertEqual(len(ha.scene_calls), 1)  # 一次调用，非逐设备


class TestPlanLandedEvent(unittest.TestCase):
    def test_plan_landed_emitted(self):
        bus = _Bus()
        ha = _HA(True)
        orch = SceneOrchestrator(ha_client=ha, bus=bus)
        orch.trigger_scene("movie")
        self.assertEqual(len(bus.events), 1)
        self.assertEqual(bus.events[0][0], "plan.landed")


class TestSceneFailure(unittest.TestCase):
    def test_failure_no_event(self):
        bus = _Bus()
        ha = _HA(False)
        orch = SceneOrchestrator(ha_client=ha, bus=bus)
        r = orch.trigger_scene("movie")
        self.assertEqual(r["status"], "fail")
        self.assertEqual(len(bus.events), 0)


if __name__ == "__main__":
    unittest.main()
