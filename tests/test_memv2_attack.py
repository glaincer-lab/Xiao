"""T9 任务包 · T9a 应对攻击/脏话/侮辱（backend/memv2 + prompts/attack.yaml）单元测试。

覆盖 DoD（影子日志硬门：先记录怎么回应，人工校准后真路由，不直接上线）：
1. 三类场景（verbal/profanity/discrimination）能被识别（detect_attack_scene）并进入
   relation_tension 信号。
2. 话术库有三类场景的应对话术，pick_attack 分别命中不同模板。
3. 姿态判定卡：DEFEND 卡已注册但默认不自动判定（影子硬门）——即便检测到攻击文本，
   classify() 仍返回 friend（不切换）。可经 activate 启用（人工校准后）。
4. 影子日志：注入 posture_controller spy，record(defend, ...) 后姿态控制器从未被调用
   （只记录、不切换，结构性无姿态副作用）。
5. ban_words 扩充：全量收拢含歧视/脏话类禁用词（回应里绝不出现这些词）。

仅标准库；MIT。
"""
from __future__ import annotations

import unittest

from backend.memv2 import posture as pos
from backend.memv2 import phrases as ph
from backend.memv2 import shadow as sh


class FakeBus:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict]] = []

    def emit(self, name: str, payload: dict) -> None:
        self.emitted.append((name, payload))


class PostureSpy:
    """姿态控制器 spy：任何调用都被记录（record 绝不能触发它）。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.current = "friend"

    def switch(self, new_posture: str) -> None:
        self.calls.append(("switch", new_posture))
        self.current = new_posture


class AttackSceneDetectTest(unittest.TestCase):
    """T9a-1: 三类攻击场景识别 + relation_tension 信号。"""

    def test_detect_verbal(self) -> None:
        self.assertEqual(pos.detect_attack_scene("你个废物猪"), "verbal")

    def test_detect_profanity(self) -> None:
        self.assertEqual(pos.detect_attack_scene("你他妈的闭嘴"), "profanity")

    def test_detect_discrimination(self) -> None:
        self.assertEqual(pos.detect_attack_scene("真是东亚病夫"), "discrimination")

    def test_no_scene_neutral(self) -> None:
        self.assertIsNone(pos.detect_attack_scene("今天天气不错"))

    def test_scene_priority_discrimination_over_profanity(self) -> None:
        # 歧视优先级高于更通用的脏话（若同词叠加也归歧视）
        self.assertEqual(pos.detect_attack_scene("你这个黑鬼滚蛋"), "discrimination")

    def test_relation_tension_signal(self) -> None:
        sig = pos.extract_signals("你个废物", {})
        self.assertTrue(sig["relation_tension"])
        self.assertFalse(pos.extract_signals("你好呀", {})["relation_tension"])


class AttackPhrasesSelectTest(unittest.TestCase):
    """T9a-2: 三类场景话术匹配（pick_attack 命中不同模板）。"""

    def test_three_scenes_exist_in_library(self) -> None:
        ps = ph.load_phrases()
        ids = {p["id"] for p in ps}
        self.assertTrue(any(i.startswith("defend_verbal_") for i in ids))
        self.assertTrue(any(i.startswith("defend_profanity_") for i in ids))
        self.assertTrue(any(i.startswith("defend_discrim_") for i in ids))

    def test_pick_attack_scenes_distinct(self) -> None:
        v = ph.pick_attack("verbal", 50)
        p = ph.pick_attack("profanity", 50)
        d = ph.pick_attack("discrimination", 50)
        self.assertTrue(v)
        self.assertTrue(p)
        self.assertTrue(d)
        self.assertNotEqual(v, d)
        # verbal 应含「设边界」语气（不接冲突）——至少不与 discrimination 同模板
        self.assertNotEqual(v, p)
        self.assertNotEqual(p, d)

    def test_pick_attack_respects_intimacy_range(self) -> None:
        # 低亲密度 → 命中 intimacy_range 含 0 的 verbal_01（[0,100]）或 verbal_02（[0,60]）
        t = ph.pick_attack("verbal", 10)
        self.assertTrue(t)

    def test_pick_attack_unknown_scene_default(self) -> None:
        self.assertEqual(ph.pick_attack("nonsense", 50), ph.DEFAULT_PHRASE)


class DefendShadowGateTest(unittest.TestCase):
    """T9a-3/4: 姿态卡硬门（注册不自动判定）+ 影子日志（记录不切换）。"""

    def test_defend_card_registered_but_not_active(self) -> None:
        clf = pos.PostureClassifier()
        self.assertIn(pos.DEFEND, clf.cards)
        self.assertNotIn(pos.DEFEND, clf.active)

    def test_attack_text_does_not_switch_to_defend(self) -> None:
        # 即便检测到关系张力，默认 classify 仍兜底 friend（影子硬门：不直接上线）。
        clf = pos.PostureClassifier()
        self.assertEqual(clf.classify("你个废物", {}), pos.FRIEND)
        self.assertNotEqual(clf.classify("你他妈的", {}), pos.DEFEND)

    def test_activate_enables_defend_for_calibration(self) -> None:
        clf = pos.PostureClassifier()
        clf.activate(pos.DEFEND)
        self.assertIn(pos.DEFEND, clf.active)
        self.assertEqual(clf.classify("你个废物猪", {}), pos.DEFEND)
        self.assertEqual(pos.DEFEND, "defend")

    def test_defend_card_scores_on_relation_tension(self) -> None:
        card = pos.PostureClassifier().cards[pos.DEFEND]
        self.assertGreater(card.enter_score({"relation_tension": True}), 0.0)
        self.assertTrue(card.should_enter({"relation_tension": True}))

    def test_shadow_records_defend_without_switching(self) -> None:
        spy = PostureSpy()
        log = sh.ShadowLog(posture_controller=spy)
        # 模拟「识别到攻击 → 记录 defend 应对」——只记录，姿态控制器不得被调用。
        log.record("s-t9a", pos.DEFEND, {"relation_tension": True}, 0.8)
        e = log.get_entries()[0]
        self.assertEqual(e["decision"], pos.DEFEND)
        self.assertEqual(e["signals"], {"relation_tension": True})
        self.assertEqual(e["score"], 0.8)
        self.assertEqual(spy.calls, [])
        self.assertEqual(spy.current, "friend")
        self.assertEqual(log._posture_controller_hits, 0)

    def test_shadow_record_emits_event(self) -> None:
        bus = FakeBus()
        log = sh.ShadowLog(bus=bus)
        log.record("s-t9a-2", pos.DEFEND, {"relation_tension": True}, 0.8)
        self.assertEqual(len(bus.emitted), 1)
        self.assertEqual(bus.emitted[0][0], sh.EVENT_POSTURE_DECISION)
        self.assertEqual(bus.emitted[0][1]["decision"], pos.DEFEND)


class BanWordsDiscriminationTest(unittest.TestCase):
    """T9a-5: ban_words 扩充歧视/脏话类词，全量可扫。"""

    def test_all_ban_words_contains_discrimination_words(self) -> None:
        words = ph.all_ban_words()
        for w in ("东亚病夫", "黑鬼", "穷鬼", "贱人", "乡巴佬"):
            self.assertIn(w, words, f"ban_words 应含歧视词: {w}")

    def test_all_ban_words_contains_profanity_words(self) -> None:
        words = ph.all_ban_words()
        for w in ("他妈的", "你妈逼", "滚蛋"):
            self.assertIn(w, words, f"ban_words 应含脏话词: {w}")

    def test_attack_templates_contain_no_ban_words(self) -> None:
        # 回应模板本身绝不能出现歧视/脏话词（只设边界，不接情绪）。
        for t in (ph.pick_attack("verbal", 50), ph.pick_attack("profanity", 50),
                  ph.pick_attack("discrimination", 50)):
            for w in ("废物", "他妈的", "东亚病夫", "黑鬼"):
                self.assertNotIn(w, t)


if __name__ == "__main__":
    unittest.main()
