"""M5-M2 审批分级 + 偏离惯例确认验收测试。"""
import unittest

from backend.m5.approval import ApprovalGate, AUTO, CONFIRM, REBUILD_NOTICE, DEVIATION_TEMPLATE


class _Perms:
    def __init__(self, granted=False):
        self.granted = granted

    def is_granted(self, category):
        return self.granted


class TestApprovalClassify(unittest.TestCase):
    def test_whitelist_granted_auto(self):
        g = ApprovalGate(perms=_Perms(True))
        self.assertEqual(g.classify("camera"), AUTO)

    def test_whitelist_outside_confirm(self):
        g = ApprovalGate(perms=_Perms(False))
        self.assertEqual(g.classify("camera"), CONFIRM)


class TestDeviationConfirm(unittest.TestCase):
    def test_deviation_confirms(self):
        profile = {"mode": "normal", "habits": [{"id": "h1", "status": "missed"}]}
        g = ApprovalGate(habit_profile=profile)
        self.assertEqual(g.deviation_confirm("h1", "obs"), DEVIATION_TEMPLATE)

    def test_no_deviation_returns_none(self):
        profile = {"mode": "normal", "habits": [{"id": "h1", "status": "active"}]}
        g = ApprovalGate(habit_profile=profile)
        self.assertIsNone(g.deviation_confirm("h1", "obs"))


class TestRebuildNotice(unittest.TestCase):
    def test_rebuild_notice(self):
        profile = {"mode": "rebuild", "habits": []}
        g = ApprovalGate(habit_profile=profile)
        self.assertEqual(g.rebuild_notice(), REBUILD_NOTICE)

    def test_normal_no_notice(self):
        profile = {"mode": "normal", "habits": []}
        g = ApprovalGate(habit_profile=profile)
        self.assertIsNone(g.rebuild_notice())


if __name__ == "__main__":
    unittest.main()
