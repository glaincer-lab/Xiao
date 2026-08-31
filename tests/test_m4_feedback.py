"""M4-M4 反馈闭环验收测试。"""
import unittest

from backend.m4.feedback import FeedbackLoop


class _Memory:
    def __init__(self):
        self.entries = []

    def record_feedback(self, entry):
        self.entries.append(entry)


class _Bus:
    def __init__(self):
        self.events = []

    def emit(self, t, p):
        self.events.append((t, p))


class TestThreeStateToMemory(unittest.TestCase):
    def test_all_three_states_recorded(self):
        mem = _Memory()
        fb = FeedbackLoop(memory=mem)
        for v in ("接受", "不接受", "部分"):
            fb.record_feedback(v, "结论")
        self.assertEqual(len(mem.entries), 3)
        self.assertEqual([e["三态"] for e in mem.entries], ["接受", "不接受", "部分"])


class TestNegativeFeedbackPriority(unittest.TestCase):
    def test_negative_marked(self):
        mem = _Memory()
        fb = FeedbackLoop(memory=mem)
        fb.record_feedback("不接受", "结论")
        self.assertTrue(mem.entries[0]["负向"])


class TestRefusalFollowupOnce(unittest.TestCase):
    def test_followup_only_once(self):
        fb = FeedbackLoop()
        self.assertTrue(fb.should_followup_refusal())
        self.assertFalse(fb.should_followup_refusal())


class TestFeedbackEvent(unittest.TestCase):
    def test_vision_feedback_emitted(self):
        bus = _Bus()
        fb = FeedbackLoop(bus=bus)
        fb.record_feedback("接受")
        self.assertEqual(len(bus.events), 1)
        self.assertEqual(bus.events[0][0], "vision.feedback")


if __name__ == "__main__":
    unittest.main()
