# -*- coding: utf-8 -*-
"""语义消歧模块单测（mock 模型依赖，验证控制流；不依赖外网模型）。

覆盖：无模型->unknown、自伤红线恒 block、技术语境->safe、真实数据->block。
真实 bge 模型精度需在有网环境执行 scripts/install_gateway_model.py 后另行回归。
"""
import unittest
from unittest import mock

import numpy as np

import backend.gateway.semantic_filter as sf


# 仅供单测的技术特征词（刻意不含裸「密码」「密钥」，否则会把真实数据误判为技术词）
_TECH = ("密码学", "密钥管理", "密钥对", "重置", "加密", "证书", "身份", "认证",
         "信息安全", "安全", "API", "加解密", "SSL", "HTTPS", "TLS", "协议",
         "校园卡", "数据库", "工程师", "数字")


def _tech_vs_real_embed(self, text):
    # 仅供单测：含技术特征词给高相似度，否则给低相似度向量
    if any(t in text for t in _TECH):
        return np.array([1.0, 0.0, 0.0])
    return np.array([0.0, 1.0, 0.0])


class JudgeContextTest(unittest.TestCase):

    def setUp(self):
        sf._ENGINE.clear()

    def test_unavailable_returns_unknown(self):
        # 模型不可用（强制 _load=False）：返回 unknown，不抛异常（fail-closed）
        with mock.patch.object(sf._Engine, "_load", return_value=False):
            self.assertEqual(sf.judge_context("学习密码学", "密码"), "unknown")

    def test_redline_always_block_even_if_model_ready(self):
        # 自伤红线：即便引擎可用也恒 block
        with mock.patch.object(sf._Engine, "_load", return_value=True):
            self.assertEqual(sf.judge_context("我不想活了", "不想活了"), "block")
            self.assertEqual(sf.judge_context("我在想自杀", "自杀"), "block")

    def test_safe_tech_context(self):
        with mock.patch.object(sf._Engine, "_load", return_value=True),              mock.patch.object(sf._Engine, "_embed", _tech_vs_real_embed):
            self.assertEqual(sf.judge_context("我在学习密码学", "密码"), "safe")
            self.assertEqual(sf.judge_context("配置CA证书与密钥对", "密钥"), "safe")

    def test_block_real_sensitive_data(self):
        with mock.patch.object(sf._Engine, "_load", return_value=True),              mock.patch.object(sf._Engine, "_embed", _tech_vs_real_embed):
            self.assertEqual(sf.judge_context("我的密码是123", "密码"), "block")
            self.assertEqual(sf.judge_context("我把密钥给了陌生人", "密钥"), "block")

    def test_semantic_error_message(self):
        # 模型可用->None；不可用->一句人话（含模型提示）
        err = sf.semantic_error()
        self.assertTrue(err is None or (isinstance(err, str) and ("model.onnx" in err or "模型" in err)))

    def test_real_model_regression(self):
        # 真实 bge 模型存在时才跑（默认设置端到端回归）；无模型则跳过
        if not sf.is_semantic_available():
            self.skipTest("本地无语义模型，跳过真实模型回归")
        self.assertEqual(sf.judge_context("学习密码学", "密码"), "safe")
        self.assertEqual(sf.judge_context("密钥管理是常识", "密钥"), "safe")
        self.assertEqual(sf.judge_context("我的密码是123", "密码"), "block")
        self.assertEqual(sf.judge_context("我把密钥给了陌生人", "密钥"), "block")
        self.assertEqual(sf.judge_context("我不想活了", "不想活了"), "block")





class RedlineMixingTest(unittest.TestCase):
    def setUp(self):
        sf._ENGINE.clear()

    def test_redline_in_text_with_non_redline_hit(self):
        # H3：命中词非红线、文本含红线 -> 必须 block（无条件扫全文红线，不依赖 hit_word）
        with mock.patch.object(sf._Engine, "_load", return_value=True), \
             mock.patch.object(sf._Engine, "_embed", _tech_vs_real_embed):
            self.assertEqual(sf.judge_context("我不想活了，顺便聊聊密码学", "密码"), "block")


class EngineCacheKeyTest(unittest.TestCase):
    def setUp(self):
        sf._ENGINE.clear()

    def test_engine_key_stable_across_fresh_configs(self):
        # H1：引擎缓存键用模型身份而非 id(cfg)；等价配置应复用同一引擎
        c1 = {"semantic": {"model_dir": "models/gateway-semantic", "model_name": "bge-small-zh-v1.5", "threshold": 0.72}}
        c2 = {"semantic": {"model_dir": "models/gateway-semantic", "model_name": "bge-small-zh-v1.5", "threshold": 0.72}}
        self.assertEqual(sf._engine_key(c1), sf._engine_key(c2))
        e1 = sf._get_engine(c1)
        e2 = sf._get_engine(c2)
        self.assertIs(e1, e2, "相同模型身份应复用同一引擎")


if __name__ == "__main__":
    unittest.main()
