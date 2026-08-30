"""M2-A 四态姿态判定卡规则引擎（backend/memv2/posture.py）单元测试。

覆盖 DoD：
1. 四态（陪伴/医护/应急/同乐）各 ≥1 进入 + 退出用例。
2. 不确定 → 默认朋友态兜底断言。
3. 用户否认 → 退朋友态且**绝不二次试探**断言。
4. 深夜时段加成（情绪词×深夜）断言 + 孤独/低落伴随时段加成。

另验证 PostureCard 接口（threshold/weight + enter_score/should_enter/should_exit/fallback）
与顾问态留接口（默认不参与、可 activate）。

仅标准库；不硬 import 未落地模块，信号由各用例自建 context/session 桩。
MIT。
"""
from __future__ import annotations

import unittest

from backend.memv2 import posture
from backend.memv2.posture import (
    CELEBRATE,
    COMPANION,
    EMERGENCY,
    FRIEND,
    MEDICAL,
    ADVISOR,
    PostureCard,
    PostureClassifier,
)


class PostureCardInterfaceTest(unittest.TestCase):
    """PostureCard 契约：threshold/weight + enter_score/should_enter/should_exit/fallback。"""

    def setUp(self):
        self.cards = PostureClassifier().cards

    def test_enter_score_weighted(self):
        companion = self.cards[COMPANION]
        self.assertAlmostEqual(companion.enter_score({"emotion_word": True}), 0.5)
        self.assertAlmostEqual(companion.enter_score({"emotion_word": False}), 0.0)
        self.assertAlmostEqual(
            companion.enter_score({"emotion_word": True, "late_night": True}), 0.9
        )

    def test_should_enter_respects_threshold(self):
        companion = self.cards[COMPANION]
        self.assertFalse(companion.should_enter({"emotion_word": True}))  # 0.5 < 0.8
        self.assertTrue(
            companion.should_enter({"emotion_word": True, "late_night": True})
        )  # 0.9 >= 0.8

    def test_friend_card_never_enters(self):
        friend = self.cards[FRIEND]
        self.assertAlmostEqual(friend.enter_score({}), 0.0)
        self.assertFalse(friend.should_enter({}))

    def test_fallback_default_friend(self):
        self.assertEqual(self.cards[COMPANION].fallback(), FRIEND)
        self.assertEqual(self.cards[MEDICAL].fallback(), FRIEND)
        self.assertEqual(self.cards[EMERGENCY].fallback(), FRIEND)
        self.assertEqual(self.cards[CELEBRATE].fallback(), FRIEND)


class CompanionPostureTest(unittest.TestCase):
    """陪伴态：进入 + 退出（连续 3 轮 >20 字无情绪词 / 用户说『好了』）。"""

    def test_entry(self):
        self.assertEqual(
            posture.classify("唉 好累 有点难过", {"hour": 23}), COMPANION
        )

    def test_exit_user_said_ok(self):
        card = PostureClassifier().cards[COMPANION]
        self.assertTrue(card.should_exit({"user_said_ok": True}))

    def test_exit_three_long_plain_turns(self):
        card = PostureClassifier().cards[COMPANION]
        self.assertTrue(card.should_exit({"consecutive_long_no_emotion": 3}))
        self.assertFalse(card.should_exit({"consecutive_long_no_emotion": 2}))

    def test_exit_default_false(self):
        card = PostureClassifier().cards[COMPANION]
        self.assertFalse(card.should_exit({}))


class MedicalPostureTest(unittest.TestCase):
    """医护态：症状词进入；症状解除或转介后退出。"""

    def test_entry(self):
        self.assertEqual(posture.classify("我今天头痛又有点发烧", {}), MEDICAL)

    def test_exit_event_cleared(self):
        card = PostureClassifier().cards[MEDICAL]
        self.assertTrue(card.should_exit({"event_cleared": True}))

    def test_exit_referred(self):
        card = PostureClassifier().cards[MEDICAL]
        self.assertTrue(card.should_exit({"referred": True}))

    def test_exit_default_false(self):
        card = PostureClassifier().cards[MEDICAL]
        self.assertFalse(card.should_exit({}))


class EmergencyPostureTest(unittest.TestCase):
    """应急态：紧急词进入；事件解除后退出。"""

    def test_entry(self):
        self.assertEqual(posture.classify("救命 我好像要晕倒了", {}), EMERGENCY)

    def test_exit_event_cleared(self):
        card = PostureClassifier().cards[EMERGENCY]
        self.assertTrue(card.should_exit({"event_cleared": True}))

    def test_exit_referred(self):
        card = PostureClassifier().cards[EMERGENCY]
        self.assertTrue(card.should_exit({"referred": True}))

    def test_exit_default_false(self):
        card = PostureClassifier().cards[EMERGENCY]
        self.assertFalse(card.should_exit({}))


class CelebratePostureTest(unittest.TestCase):
    """同乐态：好消息词+正向句式进入；情绪自然回落后退出。"""

    def test_entry(self):
        self.assertEqual(posture.classify("我考上啦 太好了", {}), CELEBRATE)

    def test_exit_emotion_eased(self):
        card = PostureClassifier().cards[CELEBRATE]
        self.assertTrue(card.should_exit({"emotion_eased": True}))

    def test_exit_default_false(self):
        card = PostureClassifier().cards[CELEBRATE]
        self.assertFalse(card.should_exit({}))


class DefaultFriendFallbackTest(unittest.TestCase):
    """不确定 → 默认朋友态兜底（最关键纪律）。"""

    def test_default_neutral_text(self):
        self.assertEqual(posture.classify("今天天气不错", {}), FRIEND)

    def test_default_empty_text(self):
        self.assertEqual(posture.classify("", {}), FRIEND)

    def test_default_unknown_context(self):
        self.assertEqual(posture.classify("帮我看看这个文件", {"hour": 15}), FRIEND)

    def test_low_emotion_only_is_not_companion(self):
        # 仅情绪词、无深夜/叹息/上下文 → 不进入陪伴态，兜底朋友态。
        self.assertEqual(posture.classify("有点难过", {"hour": 15}), FRIEND)


class LateNightBoostTest(unittest.TestCase):
    """深夜时段加成：情绪词 × 深夜 → 分数升高并进入陪伴态。"""

    def test_late_night_raises_score(self):
        companion = PostureClassifier().cards[COMPANION]
        base = companion.enter_score({"emotion_word": True})
        boosted = companion.enter_score({"emotion_word": True, "late_night": True})
        self.assertLess(base, boosted)

    def test_hour_bounds(self):
        # 非深夜（15 点）不进入；深夜（23 点）进入。
        self.assertEqual(posture.classify("有点难过", {"hour": 15}), FRIEND)
        self.assertEqual(posture.classify("有点难过", {"hour": 23}), COMPANION)

    def test_explicit_late_night_flag(self):
        self.assertEqual(
            posture.classify("有点难过", {"is_late_night": True}), COMPANION
        )


class UserDenyTest(unittest.TestCase):
    """用户否认 → 退朋友态，且绝不二次试探（被否认姿态被抑制）。"""

    def _companion_classifier(self) -> PostureClassifier:
        clf = PostureClassifier()
        # 先进入陪伴态。
        self.assertEqual(clf.classify("唉 好累 有点难过", {"hour": 23}), COMPANION)
        return clf

    def test_deny_falls_back_to_friend(self):
        clf = self._companion_classifier()
        result = clf.classify("我不是那个意思", {"user_denied": True})
        self.assertEqual(result, FRIEND)
        self.assertEqual(clf.current(), FRIEND)

    def test_no_second_probe_after_deny(self):
        clf = self._companion_classifier()
        # 用户否认 → 退朋友态并把陪伴态加入抑制集。
        self.assertEqual(clf.classify("我不是", {"user_denied": True}), FRIEND)
        # 再次出现相同情绪信号（无否认）→ 陪伴态被抑制，仍兜底朋友态。
        self.assertEqual(clf.classify("唉 好累 有点难过", {"hour": 23}), FRIEND)
        self.assertIn(COMPANION, clf.suppressed())

    def test_deny_without_entry_stays_friend(self):
        clf = PostureClassifier()
        self.assertEqual(clf.classify("你好", {"user_denied": True}), FRIEND)
        self.assertEqual(clf.suppressed(), set())


class ReservedInterfaceTest(unittest.TestCase):
    """顾问/元对话态留接口：卡已注册、默认不参与判定，可 activate 启用。"""

    def test_reserved_registered_but_not_active(self):
        clf = PostureClassifier()
        self.assertIn(ADVISOR, clf.cards)
        self.assertNotIn(ADVISOR, clf.active)

    def test_activate_enables_advisor(self):
        clf = PostureClassifier()
        clf.activate(ADVISOR)
        self.assertIn(ADVISOR, clf.active)
        self.assertEqual(clf.classify("我该怎么办", {}), ADVISOR)


if __name__ == "__main__":
    unittest.main()
