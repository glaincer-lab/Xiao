"""M4-M1 观察会话框架验收测试。"""
import unittest

from backend.m4.session import VisionSession, MAX_FRAMES


class _Auth:
    def __init__(self, enabled):
        self.enabled = enabled

    def is_granted(self, item):
        return self.enabled if item == "camera_enabled" else False


class _Bus:
    def __init__(self):
        self.events = []

    def emit(self, t, p):
        self.events.append((t, p))

    def count(self, t):
        return sum(1 for e, _ in self.events if e == t)


class _VLM:
    def __init__(self):
        self.calls = 0

    def __call__(self, text):
        self.calls += 1
        return "评论"


class TestStateMachine(unittest.TestCase):
    def test_linear_progression(self):
        s = VisionSession(auth=_Auth(True), bus=_Bus())
        for t in ("S1", "S2", "S3", "S4", "S5", "S6"):
            self.assertTrue(s.transition(t))
        self.assertEqual(s.state, "S6")

    def test_illegal_transition_raises(self):
        s = VisionSession(auth=_Auth(True), bus=_Bus())
        with self.assertRaises(ValueError):
            s.transition("S3")


class TestAuthorizationGate(unittest.TestCase):
    def test_camera_disabled_blocks_s2(self):
        s = VisionSession(auth=_Auth(False), bus=_Bus())
        s.transition("S1")
        self.assertFalse(s.transition("S2"))
        self.assertEqual(s.state, "S1")

    def test_camera_enabled_allows_s2(self):
        s = VisionSession(auth=_Auth(True), bus=_Bus())
        s.transition("S1")
        self.assertTrue(s.transition("S2"))
        self.assertEqual(s.state, "S2")


class TestFrameLifecycle(unittest.TestCase):
    def test_clear_burns_frames(self):
        s = VisionSession(auth=_Auth(True), bus=_Bus())
        for t in ("S1", "S2", "S3"):
            s.transition(t)
        s.add_frames([b"f1", b"f2"])
        self.assertEqual(s.frame_count, 2)
        s.clear()
        self.assertEqual(s.frame_count, 0)

    def test_max_24_frames(self):
        s = VisionSession(auth=_Auth(True), bus=_Bus())
        for t in ("S1", "S2", "S3"):
            s.transition(t)
        self.assertTrue(s.add_frames([b"x"] * 24))
        self.assertFalse(s.add_frames([b"y"]))
        self.assertEqual(s.frame_count, 24)


class TestZeroOutboundBeforeS2(unittest.TestCase):
    def test_no_vlm_before_s2(self):
        s = VisionSession(auth=_Auth(True), bus=_Bus(), vlm=_VLM())
        s.vlm_comment("x")  # S0
        s.transition("S1")
        s.vlm_comment("x")  # S1
        self.assertEqual(s.outbound_calls, 0)

    def test_vlm_after_s2(self):
        s = VisionSession(auth=_Auth(True), bus=_Bus(), vlm=_VLM())
        for t in ("S1", "S2", "S3"):
            s.transition(t)
        s.vlm_comment("x")
        self.assertEqual(s.outbound_calls, 1)


class TestEvents(unittest.TestCase):
    def test_session_state_emitted(self):
        bus = _Bus()
        s = VisionSession(auth=_Auth(True), bus=bus)
        self.assertEqual(bus.count("vision.session_state"), 1)
        s.transition("S1")
        self.assertEqual(bus.count("vision.session_state"), 2)

    def test_conclusion_and_feedback(self):
        bus = _Bus()
        s = VisionSession(auth=_Auth(True), bus=bus)
        for t in ("S1", "S2", "S3", "S4", "S5"):
            s.transition(t)
        s.conclude("穿搭", "总评一句")
        self.assertEqual(bus.count("vision.conclusion"), 1)
        s.transition("S6")
        s.feedback("接受")
        self.assertEqual(bus.count("vision.feedback"), 1)


if __name__ == "__main__":
    unittest.main()
