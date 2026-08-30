# -*- coding: utf-8 -*-
"""A5 网关门面单元测试（unittest）。

覆盖：黑词命中->blocked 未混淆、正常->cloud_safe 已混淆、回程 roundtrip、
不同 session 隔离、自伤红线恒不放行、语义消歧接入（safe/block/unknown 三路）、
缺 config 人话报错。需 mock 配置与语义依赖（契约签名一致的 stub）。
仅标准库；本文件 MIT。
"""
import unittest
from unittest import mock

import backend.gateway.gateway as g

KWS = ["身份证", "密码", "密钥", "bank", "自杀", "不想活了"]
MAP = {"我妈": "User_Kinship_Mother", "张总": "User_Leader_Alpha"}


def make_cfg(mapping=None, keywords=None, enabled=True):
    return {
        "compliance_gateway": {
            "enabled": enabled,
            "local_only_keywords": keywords if keywords is not None else list(KWS),
            "obfuscation_mapping": mapping if mapping is not None else dict(MAP),
            "suggested_entities_max": 5,
            "debug_log": False,
            "semantic": {
                "enabled": True,
                "backend": "onnx",
                "model_dir": "models/gateway-semantic",
                "model_name": "bge-small-zh-v1.5",
                "threshold": 0.72,
            },
        }
    }


class GuardOutboundTest(unittest.TestCase):
    def setUp(self):
        g.reset_sessions()

    def test_normal_cloud_safe_and_obfuscated(self):
        with mock.patch("backend.gateway.gateway.load_compliance", return_value=make_cfg()):
            status, processed = g.guard_outbound("我妈在开会", "s1")
        self.assertEqual(status, "cloud_safe")
        self.assertEqual(processed, "User_Kinship_Mother在开会")

    def test_black_hit_blocked_via_rule_fallback(self):
        # 语义不可用 -> 回退 allow_words 规则：敏感词未被豁免 -> blocked，且原文未混淆
        with mock.patch("backend.gateway.gateway.load_compliance", return_value=make_cfg()),              mock.patch("backend.gateway.gateway.is_semantic_available", return_value=False):
            status, processed = g.guard_outbound("我的密码是123", "s1")
        self.assertEqual(status, "blocked")
        self.assertEqual(processed, "我的密码是123")

    def test_black_hit_blocked_via_semantic_block(self):
        with mock.patch("backend.gateway.gateway.load_compliance", return_value=make_cfg()),              mock.patch("backend.gateway.gateway.is_semantic_available", return_value=True),              mock.patch("backend.gateway.gateway.judge_context", return_value="block"):
            status, processed = g.guard_outbound("我的密码是123", "s1")
        self.assertEqual(status, "blocked")
        self.assertEqual(processed, "我的密码是123")

    def test_semantic_safe_allows_tech_context(self):
        # 技术语境（密码学）经语义判定 safe -> 放行；无登记的实体，文本不变
        with mock.patch("backend.gateway.gateway.load_compliance", return_value=make_cfg()),              mock.patch("backend.gateway.gateway.is_semantic_available", return_value=True),              mock.patch("backend.gateway.gateway.judge_context", return_value="safe"):
            status, processed = g.guard_outbound("我在学习密码学", "s1")
        self.assertEqual(status, "cloud_safe")
        self.assertEqual(processed, "我在学习密码学")

    def test_semantic_unknown_falls_back_to_rule(self):
        # unknown -> 回退 allow_words：被豁免词覆盖（密码学）-> 放行
        with mock.patch("backend.gateway.gateway.load_compliance", return_value=make_cfg()),              mock.patch("backend.gateway.gateway.is_semantic_available", return_value=True),              mock.patch("backend.gateway.gateway.judge_context", return_value="unknown"):
            status, processed = g.guard_outbound("我在学习密码学", "s1")
        self.assertEqual(status, "cloud_safe")
        self.assertEqual(processed, "我在学习密码学")
        # unknown + 未被豁免 -> blocked
        with mock.patch("backend.gateway.gateway.load_compliance", return_value=make_cfg()),              mock.patch("backend.gateway.gateway.is_semantic_available", return_value=True),              mock.patch("backend.gateway.gateway.judge_context", return_value="unknown"):
            status2, processed2 = g.guard_outbound("我的密码是123", "s2")
        self.assertEqual(status2, "blocked")
        self.assertEqual(processed2, "我的密码是123")

    def test_self_harm_always_blocked_even_if_semantic_safe(self):
        # 自伤红线：即便语义返回 safe 也恒不放行
        with mock.patch("backend.gateway.gateway.load_compliance", return_value=make_cfg()),              mock.patch("backend.gateway.gateway.is_semantic_available", return_value=True),              mock.patch("backend.gateway.gateway.judge_context", return_value="safe"):
            status, processed = g.guard_outbound("我不想活了", "s1")
        self.assertEqual(status, "blocked")
        self.assertEqual(processed, "我不想活了")

    def test_obfuscate_only_lowers_black_word(self):
        # 未命中黑词但有登记实体 -> cloud_safe 且混淆
        with mock.patch("backend.gateway.gateway.load_compliance", return_value=make_cfg()):
            status, processed = g.guard_outbound("张总的方案", "s1")
        self.assertEqual(status, "cloud_safe")
        self.assertEqual(processed, "User_Leader_Alpha的方案")


class GuardInboundTest(unittest.TestCase):
    def setUp(self):
        g.reset_sessions()

    def test_roundtrip(self):
        with mock.patch("backend.gateway.gateway.load_compliance", return_value=make_cfg()):
            status, processed = g.guard_outbound("我妈在开会", "s1")
            restored = g.guard_inbound(processed, "s1")
        self.assertEqual(status, "cloud_safe")
        self.assertEqual(restored, "我妈在开会")

    def test_roundtrip_two_entities_combined(self):
        # 同 session 两次出网登记两类实体；云端返回同时引用两者 -> 还原正确且校验通过
        with mock.patch("backend.gateway.gateway.load_compliance", return_value=make_cfg()):
            _, p1 = g.guard_outbound("我妈在开会", "s1")
            _, p2 = g.guard_outbound("张总在开会", "s1")
            combined = p1 + "，" + p2
            restored = g.guard_inbound(combined, "s1")
        self.assertEqual(restored, "我妈在开会，张总在开会")


class SessionIsolationTest(unittest.TestCase):
    def setUp(self):
        g.reset_sessions()

    def test_two_sessions_do_not_interfere(self):
        with mock.patch("backend.gateway.gateway.load_compliance", return_value=make_cfg()):
            _, pa = g.guard_outbound("我妈在开会", "A")
            _, pb = g.guard_outbound("张总在开会", "B")
            ra = g.guard_inbound(pa, "A")
            rb = g.guard_inbound(pb, "B")
        self.assertEqual(ra, "我妈在开会")
        self.assertEqual(rb, "张总在开会")
        # A/B 的占位符互不串扰：A 的返回不含 B 的占位符，各还原各的
        self.assertNotIn("User_Leader_Alpha", ra)
        self.assertNotIn("User_Kinship_Mother", rb)

    def test_get_session_context_is_per_session(self):
        a = g.get_session_context("A")
        b = g.get_session_context("B")
        a2 = g.get_session_context("A")
        self.assertIsNot(a, b, "不同 session 应返回不同实例")
        self.assertIs(a, a2, "同一 session 懒加载应返回同一实例")


class ConfigErrorTest(unittest.TestCase):
    def test_missing_config_raises_human_error(self):
        with mock.patch(
            "backend.gateway.gateway.load_compliance",
            side_effect=Exception("配置文件 compliance.yaml 不存在"),
        ):
            with self.assertRaises(g.GatewayConfigError) as cm:
                g.guard_outbound("你好", "s1")
        msg = str(cm.exception)
        self.assertIn("无法加载合规配置", msg)
        self.assertIn("compliance.yaml", msg)
        self.assertTrue("下一步" in msg or "请检查" in msg)

    def test_disabled_gateway_passthrough(self):
        # 总开关关闭 -> 直通（不混淆不拦截）
        with mock.patch("backend.gateway.gateway.load_compliance", return_value=make_cfg(enabled=False)):
            status, processed = g.guard_outbound("我的密码是123", "s1")
        self.assertEqual(status, "cloud_safe")
        self.assertEqual(processed, "我的密码是123")





class SelfHarmRedlineTest(unittest.TestCase):
    def setUp(self):
        g.reset_sessions()

    def test_disabled_gateway_still_blocks_self_harm(self):
        # H2：总开关关闭也须拦截自伤红线
        with mock.patch("backend.gateway.gateway.load_compliance", return_value=make_cfg(enabled=False)):
            status, processed = g.guard_outbound("我不想活了", "s1")
        self.assertEqual(status, "blocked")
        self.assertEqual(processed, "我不想活了")


class MultiRoundNoFalseMismatchTest(unittest.TestCase):
    def setUp(self):
        g.reset_sessions()

    def test_interleaved_rounds_no_false_mismatch(self):
        # M1：出网→回程逐轮隔离，不应因旧占位符误报 mismatch
        with mock.patch("backend.gateway.gateway.load_compliance", return_value=make_cfg()):
            _, p1 = g.guard_outbound("我妈在开会", "s1")
            r1 = g.guard_inbound(p1, "s1")
            _, p2 = g.guard_outbound("张总在开会", "s1")
            r2 = g.guard_inbound(p2, "s1")
        self.assertEqual(r1, "我妈在开会")
        self.assertEqual(r2, "张总在开会")
        self.assertEqual(g.get_session_context("s1")._mismatch_log, [])


if __name__ == "__main__":
    unittest.main()
