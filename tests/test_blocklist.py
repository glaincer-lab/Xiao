# -*- coding: utf-8 -*-
"""A2 黑词检测纯函数单元测试（unittest）。

覆盖契约、消歧与性能要求，详见 _M0-tasks/A2-blocklist-engine.md。
"""
import time
import unittest

from backend.gateway.blocklist import detect_blocked

# 契约黑词表（来自 compliance.yaml 的 local_only_keywords）
KWS = ["自杀", "不想活了", "身份证", "密码", "密钥", "bank"]
# 误拦豁免词（经由 A5 从配置/词库传入，见模块 docstring 的消歧说明）
ALLOW_CN = ["密码学", "密钥管理"]


class DetectBlockedTest(unittest.TestCase):

    def test_hit_self_harm_words(self):
        self.assertEqual(detect_blocked("我真的不想活了", ["自杀", "不想活了"]), "不想活了")
        self.assertEqual(detect_blocked("我在想自杀", ["自杀", "不想活了"]), "自杀")

    def test_hit_sensitive_id_card(self):
        self.assertEqual(detect_blocked("我的身份证号 110101...", ["身份证"]), "身份证")
        self.assertEqual(detect_blocked("请出示身份证", ["身份证"]), "身份证")

    def test_hit_sensitive_password(self):
        # 黑词嵌在句中也能命中（子串匹配，且不被白名单覆盖）
        self.assertEqual(detect_blocked("我的密码是123", KWS, ALLOW_CN), "密码")
        self.assertEqual(detect_blocked("请把密钥给我", KWS, ALLOW_CN), "密钥")

    def test_hit_english_bank(self):
        self.assertEqual(detect_blocked("my bank password", ["bank"]), "bank")

    def test_hit_english_case_insensitive(self):
        for text in ("BANK account", "Bank account", "bank account"):
            self.assertEqual(detect_blocked(text, ["bank"]), "bank", text)

    def test_disambiguate_cn_composite(self):
        # 从根本上解决误拦：命中位置落在豁免词内 -> 放行
        self.assertIsNone(detect_blocked("学习密码学", KWS, ALLOW_CN))
        self.assertIsNone(detect_blocked("密钥管理是常识", KWS, ALLOW_CN))
        self.assertIsNone(detect_blocked("他在专攻密码学方向", KWS, ALLOW_CN))

    def test_disambiguate_does_not_undercut_block(self):
        # 白名单只豁免它覆盖的那些词，其余照常拦截
        self.assertEqual(detect_blocked("我的密码是123", KWS, ALLOW_CN), "密码")
        self.assertEqual(detect_blocked("他的密码从来没有暴露", KWS, ALLOW_CN), "密码")
        self.assertEqual(detect_blocked("the bank is closed", ["bank"], ["Bank account"]), "bank")

    def test_disambiguate_english_allow_case_insensitive(self):
        # 英文豁免词也大小写不敏感
        self.assertIsNone(detect_blocked("学习 Bank account", ["bank"], ["Bank account"]))

    def test_miss_no_keyword_in_text(self):
        self.assertIsNone(detect_blocked("今天吃了蛋糕，心情很好", KWS))
        self.assertIsNone(detect_blocked("我们聊一下天气吧", KWS))

    def test_miss_when_keyword_absent_from_table(self):
        kws = ["自杀", "不想活了", "身份证", "bank"]
        self.assertIsNone(detect_blocked("学习密码学", kws))
        self.assertIsNone(detect_blocked("密钥管理是常识", kws))

    def test_cn_substring_contract_without_allow(self):
        # 不传 allow_words 时=朴素子串匹配：
        # 「密码学」含子串「密码」、「密钥管理」含子串「密钥」-> 命中。
        # 要消除这类误拦，需显式传入豁免词（见 test_disambiguate_cn_composite）。
        self.assertEqual(detect_blocked("学习密码学", ["密码"]), "密码")
        self.assertEqual(detect_blocked("密钥管理是常识", ["密钥"]), "密钥")

    def test_miss_empty_keywords(self):
        self.assertIsNone(detect_blocked("我想自杀", []))
        self.assertIsNone(detect_blocked("my bank password", []))

    def test_miss_empty_text(self):
        self.assertIsNone(detect_blocked("", ["自杀", "bank"]))

    def test_first_hit_by_text_position(self):
        self.assertEqual(detect_blocked("先身份证后密码", ["密码", "身份证"]), "身份证")

    def test_mixed_cn_en_long_text_hit(self):
        text = "今天天气不错 " + "bank " * 3 + " 后面还有中文内容提到身份证和不想活了的事情。"
        self.assertEqual(detect_blocked(text, ["bank", "身份证", "不想活了"]), "bank")

    def test_performance_long_text(self):
        # 10 万字级文本 < 100ms（带与不带白名单均测）
        text = "这是一段正常的聊天内容，完全不涉及敏感信息。" * 8000
        self.assertGreater(len(text), 100000, "文本应超过 10 万字")
        start = time.perf_counter()
        r1 = detect_blocked(text, KWS)
        r2 = detect_blocked(text, KWS, ["密码学", "密钥管理", "Bank account"])
        ms = (time.perf_counter() - start) * 1000
        self.assertIsNone(r1)
        self.assertIsNone(r2)
        self.assertLess(ms, 100, f"10 万字检测耗时 {ms:.1f}ms，应 <100ms")





class EnglishBoundaryTest(unittest.TestCase):
    def test_english_substring_no_longer_matches(self):
        # M4：英文按词边界，substring 不再误伤 bankruptcy/banker/banking
        self.assertIsNone(detect_blocked("the company went bankruptcy", ["bank"]))
        self.assertIsNone(detect_blocked("he is a banker", ["bank"]))
        self.assertIsNone(detect_blocked("the banking sector grew", ["bank"]))
        self.assertEqual(detect_blocked("my bank account", ["bank"]), "bank")
        self.assertEqual(detect_blocked("Bank of China", ["bank"]), "bank")


if __name__ == "__main__":
    unittest.main()
