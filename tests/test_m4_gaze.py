"""M4-M3 视线对齐验收测试。"""
import unittest

from backend.m4.gaze import gaze_offset


class TestGazeOffset(unittest.TestCase):
    def test_position_dict(self):
        self.assertEqual(gaze_offset({"位置": {"x": 0.5, "y": -0.3}}), {"x": 0.5, "y": -0.3})

    def test_position_english_key(self):
        self.assertEqual(gaze_offset({"position": {"x": 0.2, "y": 0.8}}), {"x": 0.2, "y": 0.8})

    def test_position_list(self):
        self.assertEqual(gaze_offset({"位置": [0.2, 0.8]}), {"x": 0.2, "y": 0.8})

    def test_no_position_returns_none(self):
        self.assertIsNone(gaze_offset({"session_id": "s", "state": "S3"}))

    def test_clamped(self):
        self.assertEqual(gaze_offset({"位置": {"x": 5.0, "y": -9.0}}), {"x": 1.0, "y": -1.0})

    def test_invalid(self):
        self.assertIsNone(gaze_offset({"位置": "not-a-position"}))


if __name__ == "__main__":
    unittest.main()
