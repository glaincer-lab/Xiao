"""M1-D 听错入口分级（backend/memv1/mishearing.py）单元测试。

规格本源：docs/specs/M1-memory.md §4.3 + MEMV1_CONTRACT.md 的 confirmed 字段。

覆盖验收断言：
1. 高风险 100% 出复述确认（needs_confirmation==high 时 build_confirm_utterance 必为确认话术）；
2. confirmed 只在确认后置 true（apply_confirmation 为唯一置真入口，未确认永 false）；
3. 音频零留存（AUDIO_RETENTION_FORBIDDEN 为真、should_retain_audio 恒返回 False）。

仅标准库（unittest）。MemEntry 未落地，测试按契约字段自建最小桩，不硬 import 依赖。
"""
from __future__ import annotations

import unittest
from dataclasses import dataclass

from backend.memv1.mishearing import (
    AUDIO_RETENTION_FORBIDDEN,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    apply_confirmation,
    build_confirm_utterance,
    classify_risk,
    needs_confirmation,
    should_retain_audio,
)


# 按 MEMV1_CONTRACT §3 自建的最小桩（MemEntry 可能未落地，不硬 import）
@dataclass
class _StubMemEntry:
    """按契约字段自建的最小 MemEntry 桩，仅测试 confirmed 门控。"""

    content: str
    confirmed: bool = False


# 高风险用例：每个都必须 100% 出复述确认
HIGH_CASES = [
    "记住我四月十二号生日",
    "我以后不喝咖啡了",
    "我的密码是123456",
    "把那个文件彻底删掉",
    "别忘了帮我买牛奶",
    "张老师明天上午十点开会",
    "下周三下午三点开会",
]

# 中风险（可逆任务）
MEDIUM_CASES = [
    "帮我定个早上七点的闹钟",
    "给我来两杯咖啡",
    "播放周杰伦的歌",
    "翻译这句话成英文",
    "提醒我下午三点喝水",
]

# 低风险（闲聊）
LOW_CASES = [
    "今天天气不错",
    "你好呀",
    "随便聊聊",
    "哈哈",
]


class ClassifyRiskTest(unittest.TestCase):
    def test_memory_write_is_high(self) -> None:
        for t in ("记住我四月十二号生日", "我以后不喝咖啡了", "别忘了帮我买牛奶"):
            self.assertEqual(classify_risk(t), RISK_HIGH, t)

    def test_date_num_name_high_risk(self) -> None:
        # 独立数字/日期/人名陈述（非任务语境）→ 高
        for t in ("我的密码是123456", "张老师明天上午十点开会", "下周三下午三点开会"):
            self.assertEqual(classify_risk(t), RISK_HIGH, t)

    def test_irreversible_is_high(self) -> None:
        self.assertEqual(classify_risk("把那个文件彻底删掉"), RISK_HIGH)
        self.assertEqual(classify_risk("注销我的账户"), RISK_HIGH)
        self.assertEqual(classify_risk("清空全部提醒"), RISK_HIGH)

    def test_reversible_task_is_medium(self) -> None:
        for t in MEDIUM_CASES:
            self.assertEqual(classify_risk(t), RISK_MEDIUM, t)

    def test_smalltalk_is_low(self) -> None:
        for t in LOW_CASES:
            self.assertEqual(classify_risk(t), RISK_LOW, t)

    def test_anomaly_signals_raise_to_high(self) -> None:
        # §4.3 检测信号：语义残句 / 时长字数比异常 / VAD 切碎 → 高
        self.assertEqual(
            classify_risk("买咖", {"is_semantic_fragment": True}), RISK_HIGH
        )
        self.assertEqual(
            classify_risk("嗯", {"duration_seconds": 8.0, "char_count": 1}), RISK_HIGH
        )
        self.assertEqual(classify_risk("这个", {"vad_fragments": 5}), RISK_HIGH)

    def test_anomaly_signal_in_task_still_high(self) -> None:
        # 即使含任务动词，检测信号异常会覆盖为高（防听错落地）
        self.assertEqual(
            classify_risk("买牛奶", {"is_semantic_fragment": True}), RISK_HIGH
        )

    def test_does_not_depend_on_asr_confidence(self) -> None:
        # §4.3：ASR 置信度字段核实前不依赖 —— 传任何置信度不改变结果
        base = classify_risk("记住我四月十二号生日")
        for conf in (0.01, 0.5, 0.99, None):
            self.assertEqual(
                classify_risk("记住我四月十二号生日", {"asr_confidence": conf}), base
            )

    def test_empty_text_is_low(self) -> None:
        self.assertEqual(classify_risk(""), RISK_LOW)

    def test_needs_confirmation_matches_high(self) -> None:
        self.assertTrue(needs_confirmation("记住我四月十二号生日"))
        self.assertFalse(needs_confirmation("帮我定个闹钟"))
        self.assertFalse(needs_confirmation("今天天气不错"))


class ConfirmUtteranceTest(unittest.TestCase):
    def test_high_risk_always_yields_confirm_utterance(self) -> None:
        # 验收断言：高风险 100% 出复述确认
        for t in HIGH_CASES:
            self.assertEqual(classify_risk(t), RISK_HIGH, t)
            utt = build_confirm_utterance(t)
            self.assertTrue(utt.strip(), f"高风险 {t} 未生成确认话术")
            self.assertIn("对吧", utt, f"确认话术缺问句：{utt!r}")

    def test_build_confirm_utterance_strips_memory_write_prefix(self) -> None:
        self.assertEqual(build_confirm_utterance("记住我四月十二号生日"), "我四月十二号生日，对吧？")

    def test_build_confirm_utterance_keeps_plain_statement(self) -> None:
        self.assertEqual(build_confirm_utterance("把那个文件彻底删掉"), "把那个文件彻底删掉，对吧？")

    def test_already_ask_stays_unchanged(self) -> None:
        self.assertEqual(build_confirm_utterance("四月十二对吧"), "四月十二对吧")

    def test_confirm_utterance_not_empty_for_all_high_risk(self) -> None:
        for t in HIGH_CASES:
            self.assertTrue(build_confirm_utterance(t).strip(), t)


class ConfirmationGateTest(unittest.TestCase):
    def test_confirmed_stays_false_until_user_confirms(self) -> None:
        # 高风险条目未确认前 confirmed 恒 false
        entry = _StubMemEntry("我以后不喝咖啡了")
        self.assertTrue(needs_confirmation(entry.content))
        # 未确认 → 保持 false
        self.assertFalse(entry.confirmed)
        self.assertFalse(apply_confirmation(False))
        entry.confirmed = apply_confirmation(False)
        self.assertFalse(entry.confirmed)

    def test_confirmed_only_after_user_confirms(self) -> None:
        entry = _StubMemEntry("记住我四月十二号生日")
        # 用户未确认时绝不自动置 true
        entry.confirmed = apply_confirmation(False)
        self.assertFalse(entry.confirmed)
        # 用户确认后 → true
        entry.confirmed = apply_confirmation(True)
        self.assertTrue(entry.confirmed)

    def test_medium_or_low_never_require_confirmation(self) -> None:
        for t in MEDIUM_CASES + LOW_CASES:
            self.assertFalse(needs_confirmation(t), t)


class AudioZeroRetentionTest(unittest.TestCase):
    def test_retention_forbidden_constant(self) -> None:
        self.assertTrue(AUDIO_RETENTION_FORBIDDEN)

    def test_never_retain_audio_any_risk(self) -> None:
        # 音频零留存：高/中/低任意输入，should_retain_audio 恒 False
        for t in HIGH_CASES + MEDIUM_CASES + LOW_CASES:
            self.assertFalse(should_retain_audio(t), t)

    def test_never_retain_even_with_signals(self) -> None:
        self.assertFalse(
            should_retain_audio("嗯", {"duration_seconds": 8.0, "char_count": 1})
        )
        self.assertFalse(
            should_retain_audio("买咖", {"is_semantic_fragment": True})
        )

    def test_classify_returns_plain_str_no_audio_payload(self) -> None:
        # 分类结果仅为一个字符串，绝不携带任何音频对象 / 文件引用
        for t in HIGH_CASES:
            result = classify_risk(t)
            self.assertIsInstance(result, str)
            self.assertNotIn("audio", result)
        self.assertEqual(
            set(map(classify_risk, HIGH_CASES)), {RISK_HIGH}
        )


if __name__ == "__main__":
    unittest.main()
