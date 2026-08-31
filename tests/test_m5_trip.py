"""M5-M5 行程编排验收测试。"""
import unittest

from backend.m5.trip import TripPlanner, MAX_OPTIONS


class _Weather:
    def __init__(self, forecast=None):
        self.forecast = forecast

    def get_forecast(self, dest, date):
        return self.forecast


class _Calendar:
    def __init__(self):
        self.events = []

    def add_event(self, option):
        self.events.append(option)
        return True


class TestOptionsAtMostThree(unittest.TestCase):
    def test_options_with_cost_and_confidence(self):
        p = TripPlanner(weather=_Weather({"temp": 20}))
        r = p.plan("上海", "2026-09-01")
        self.assertLessEqual(len(r["options"]), MAX_OPTIONS)
        for o in r["options"]:
            self.assertIn("代价", o)
            self.assertIn("置信度", o)


class TestRecommend(unittest.TestCase):
    def test_recommend_highest_confidence(self):
        p = TripPlanner(weather=_Weather({"temp": 20}))
        r = p.plan("上海", "2026-09-01")
        rec = p.recommend(r["options"])
        self.assertEqual(rec["方案"], "A")


class TestLandActionExplicit(unittest.TestCase):
    def test_land_with_calendar(self):
        cal = _Calendar()
        p = TripPlanner(calendar=cal)
        r = p.land({"方案": "A"})
        self.assertTrue(r["landed"])
        self.assertEqual(len(cal.events), 1)

    def test_no_calendar_explicit(self):
        p = TripPlanner()  # 无 calendar
        r = p.land({"方案": "A"})
        self.assertFalse(r["landed"])
        self.assertIn("不落地", r["message"])


class TestWeatherFailureDegrades(unittest.TestCase):
    def test_weather_missing(self):
        p = TripPlanner(weather=_Weather(None))
        r = p.plan("上海", "2026-09-01")
        self.assertIsNotNone(r["weather_missing"])


if __name__ == "__main__":
    unittest.main()
