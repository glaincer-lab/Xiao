"""M5-M6 米家直连验收测试。"""
import unittest

from backend.m5.mijia import MijiaClient, MAX_FAILURES, STOP_MESSAGE


class _Fail:
    def __call__(self, entity, action):
        return False


class _Ok:
    def __call__(self, entity, action):
        return True


class TestDefaultDisabled(unittest.TestCase):
    def test_disabled_by_default(self):
        c = MijiaClient()
        self.assertFalse(c.enabled)
        r = c.execute("light", "on")
        self.assertEqual(r["status"], "disabled")


class TestWarningConfigurable(unittest.TestCase):
    def test_custom_warning(self):
        c = MijiaClient(warning="自定义文案")
        self.assertEqual(c.warning, "自定义文案")


class TestTokenFromEnv(unittest.TestCase):
    def test_token_not_hardcoded(self):
        c = MijiaClient()
        self.assertEqual(c.token, "")


class TestFailThreeTimesStop(unittest.TestCase):
    def test_stop_after_three(self):
        c = MijiaClient(enabled=True, caller=_Fail())
        for _ in range(MAX_FAILURES):
            c.execute("light", "on")
        r = c.execute("light", "on")
        self.assertEqual(r["status"], "stopped")
        self.assertEqual(r["message"], STOP_MESSAGE)


class TestOk(unittest.TestCase):
    def test_ok(self):
        c = MijiaClient(enabled=True, caller=_Ok())
        r = c.execute("light", "on")
        self.assertEqual(r["status"], "ok")


if __name__ == "__main__":
    unittest.main()
