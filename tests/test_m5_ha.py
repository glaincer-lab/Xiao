"""M5-M1 HA 主路径接入验收测试。"""
import unittest

from backend.m5.ha_client import (
    HAClient,
    HAActionCoordinator,
    READBACK_SECONDS,
    READBACK_MISMATCH_MSG,
)


class FakeClient:
    def __init__(self, pre_state="off", post_state="on", call_ok=True):
        self.pre_state = pre_state
        self.post_state = post_state
        self.call_ok = call_ok
        self.get_calls = 0
        self.call_calls = 0
        self.readback_calls = 0
        self.readback_timeout = None

    def get_state(self, entity):
        self.get_calls += 1
        return self.pre_state

    def call_service(self, entity, action):
        self.call_calls += 1
        return self.call_ok

    def readback(self, entity, timeout=None):
        self.readback_calls += 1
        self.readback_timeout = timeout
        return self.post_state


class TestPreStateBeforeExecute(unittest.TestCase):
    def test_pre_state_queried(self):
        c = FakeClient()
        coord = HAActionCoordinator(client=c, whitelist=["light.a"])
        coord.execute("light.a", "on")
        self.assertGreaterEqual(c.get_calls, 1)


class TestReadbackAfterExecute(unittest.TestCase):
    def test_readback_called_5s(self):
        c = FakeClient()
        coord = HAActionCoordinator(client=c, whitelist=["light.a"])
        coord.execute("light.a", "on")
        self.assertEqual(c.readback_calls, 1)
        self.assertEqual(c.readback_timeout, READBACK_SECONDS)


class TestReadbackMismatch(unittest.TestCase):
    def test_mismatch_reports_once(self):
        c = FakeClient(pre_state="off", post_state="off")
        coord = HAActionCoordinator(client=c, whitelist=["light.a"])
        r = coord.execute("light.a", "on")
        self.assertEqual(r["status"], "mismatch")
        self.assertEqual(r["message"], READBACK_MISMATCH_MSG)


class TestWhitelist(unittest.TestCase):
    def test_whitelist_outside_no_execute(self):
        c = FakeClient()
        coord = HAActionCoordinator(client=c, whitelist=["light.a"])
        r = coord.execute("light.b", "on", confirm=False)
        self.assertEqual(r["status"], "suggest")
        self.assertEqual(c.call_calls, 0)

    def test_whitelist_inside_auto_execute(self):
        c = FakeClient(post_state="on")
        coord = HAActionCoordinator(client=c, whitelist=["light.a"])
        r = coord.execute("light.a", "on", confirm=False)
        self.assertEqual(r["status"], "ok")


class TestOffline(unittest.TestCase):
    def test_offline_message(self):
        c = FakeClient(pre_state=None)
        coord = HAActionCoordinator(client=c, whitelist=["light.a"])
        r = coord.execute("light.a", "on")
        self.assertEqual(r["status"], "offline")


class TestTokenNotHardcoded(unittest.TestCase):
    def test_token_from_env(self):
        client = HAClient()
        self.assertEqual(client.token, "")


if __name__ == "__main__":
    unittest.main()
