"""M3-M5 主动风格画像验收测试。"""
import datetime as _dt
import unittest

from backend.m3.style import StyleProfile, DEFAULT_RESPONSE_RATE


class _Clock:
    def __init__(self, start: _dt.datetime) -> None:
        self.now = start

    def __call__(self) -> _dt.datetime:
        return self.now


class TestFieldsNoPsychLabels(unittest.TestCase):
    def test_fields_strict_three(self):
        sp = StyleProfile()
        self.assertEqual(
            sp.fields(),
            frozenset(("response_rate", "preferred_density", "override_by_user")),
        )
        self.assertEqual(
            set(sp.snapshot().keys()),
            {"response_rate", "preferred_density", "override_by_user"},
        )

    def test_defaults(self):
        sp = StyleProfile()
        self.assertEqual(sp.response_rate, DEFAULT_RESPONSE_RATE)
        self.assertEqual(sp.preferred_density, "med")
        self.assertFalse(sp.override_by_user)


class TestOverridePausesAdaptation(unittest.TestCase):
    def test_override_blocks_learning(self):
        clock = _Clock(_dt.datetime(2026, 8, 1, 12, 0, 0))
        sp = StyleProfile(now_fn=clock)
        sp.set_override()
        before = sp.snapshot()
        clock.now = _dt.datetime(2026, 8, 2, 12, 0, 0)
        sp.record_delivery()
        sp.record_response()
        sp.learn()
        self.assertEqual(sp.snapshot(), before)

    def test_clear_override_resumes(self):
        sp = StyleProfile(density_source=lambda: "low")
        sp.set_override()
        sp.learn()
        self.assertEqual(sp.preferred_density, "med")
        sp.clear_override()
        sp.learn()
        self.assertEqual(sp.preferred_density, "low")


class TestResponseRate(unittest.TestCase):
    def test_window_30_days(self):
        clock = _Clock(_dt.datetime(2026, 8, 31, 12, 0, 0))
        sp = StyleProfile(now_fn=clock)
        sp.record_delivery(_dt.datetime(2026, 8, 31, 10, 0, 0))
        sp.record_delivery(_dt.datetime(2026, 8, 31, 11, 0, 0))
        sp.record_response(_dt.datetime(2026, 8, 31, 11, 5, 0))
        self.assertEqual(sp.response_rate, round(1 / 2, 3))

    def test_window_excludes_old(self):
        clock = _Clock(_dt.datetime(2026, 8, 31, 12, 0, 0))
        sp = StyleProfile(now_fn=clock)
        sp.record_delivery(_dt.datetime(2026, 7, 1, 10, 0, 0))
        sp.record_response(_dt.datetime(2026, 7, 1, 10, 1, 0))
        self.assertEqual(sp.response_rate, DEFAULT_RESPONSE_RATE)


class TestHeartbeatReverse(unittest.TestCase):
    class _FakeHeartbeat:
        def __init__(self, frequency="daily"):
            self.frequency = frequency

    def test_low_response_decays(self):
        clock = _Clock(_dt.datetime(2026, 8, 31, 12, 0, 0))
        sp = StyleProfile(now_fn=clock)
        for i in range(3):
            sp.record_delivery(_dt.datetime(2026, 8, 31, 10, i, 0))
        hb = self._FakeHeartbeat("daily")
        sp.apply_to_heartbeat(hb)
        self.assertEqual(hb.frequency, "every_2_days")

    def test_high_response_keeps(self):
        clock = _Clock(_dt.datetime(2026, 8, 31, 12, 0, 0))
        sp = StyleProfile(now_fn=clock)
        sp.record_delivery(_dt.datetime(2026, 8, 31, 10, 0, 0))
        sp.record_response(_dt.datetime(2026, 8, 31, 10, 1, 0))
        hb = self._FakeHeartbeat("daily")
        sp.apply_to_heartbeat(hb)
        self.assertEqual(hb.frequency, "daily")


class TestHabitProfileReadonly(unittest.TestCase):
    def test_readonly(self):
        class _Store:
            def __init__(self):
                self.habit_calls = 0
                self.add_habit_calls = 0

            def habit_profile(self):
                self.habit_calls += 1
                return {"mode": "normal", "rebuild_reason": "", "habits": []}

            def add_habit(self, content, category="一般"):
                self.add_habit_calls += 1
                return {}

        store = _Store()
        sp = StyleProfile()
        profile = sp.bind_habit_profile(store)
        self.assertEqual(profile["mode"], "normal")
        self.assertEqual(store.habit_calls, 1)
        self.assertEqual(store.add_habit_calls, 0)


if __name__ == "__main__":
    unittest.main()
