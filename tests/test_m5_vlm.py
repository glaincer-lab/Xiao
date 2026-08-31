"""M5-M4 VLM 兜底操电脑验收测试。"""
import unittest

from backend.m5.vlm_operator import VlmOperator, scale_coords, is_sensitive_page


class TestScaleCoords(unittest.TestCase):
    def test_scale(self):
        self.assertEqual(scale_coords((320, 180), (640, 360), (1920, 1080)), (960, 540))


class TestSensitivePage(unittest.TestCase):
    def test_sensitive(self):
        self.assertTrue(is_sensitive_page("请输入支付密码"))
        self.assertTrue(is_sensitive_page("Checkout page"))

    def test_not_sensitive(self):
        self.assertFalse(is_sensitive_page("普通网页内容"))


class _Screen:
    def __init__(self, before="A", after="B"):
        self.before = before
        self.after = after
        self.executed = []

    def capture(self):
        return self.after if self.executed else self.before

    def execute(self, coord):
        self.executed.append(coord)


class _VLM:
    def __init__(self, coord=(10, 10)):
        self.coord = coord

    def locate(self, image):
        return self.coord


class TestVlmOperator(unittest.TestCase):
    def test_diff_verify_mismatch(self):
        screen = _Screen(before="A", after="A")  # 执行后不变
        op = VlmOperator(screen=screen, vlm=_VLM())
        r = op.run("click")
        self.assertEqual(r["status"], "mismatch")

    def test_sensitive_blocked(self):
        screen = _Screen(before="支付页面")
        op = VlmOperator(screen=screen, vlm=_VLM())
        r = op.run("click")
        self.assertEqual(r["status"], "blocked")

    def test_confirm_denied(self):
        screen = _Screen()
        op = VlmOperator(screen=screen, vlm=_VLM(), confirm=lambda a: False)
        r = op.run("click")
        self.assertEqual(r["status"], "denied")
        self.assertEqual(len(screen.executed), 0)

    def test_ok(self):
        screen = _Screen(before="A", after="B")
        op = VlmOperator(screen=screen, vlm=_VLM())
        r = op.run("click")
        self.assertEqual(r["status"], "ok")


if __name__ == "__main__":
    unittest.main()
