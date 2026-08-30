# -*- coding: utf-8 -*-
"""A4 会话级占位符清单 + 还原校验单元测试（unittest）。

覆盖 contract 要求的：正常 roundtrip、占位符被改写->verify False 且有日志、
两个 session 互不干扰、同 session 两次 register 各计数、空 mapping、load_compliance_for。
依赖仅标准库；本文件 MIT。
"""
import json
import unittest

from backend.gateway.obfuscate import obfuscate
from backend.gateway.session_manifest import SessionContext, load_compliance_for

M = {"我妈": "User_Kinship_Mother"}


class SessionRegisterVerifyTest(unittest.TestCase):
    def test_register_returns_count(self):
        ctx = SessionContext("s1", "n1")
        self.assertEqual(ctx.register("我妈在开会", M), 1)

    def test_register_empty_mapping_returns_zero(self):
        ctx = SessionContext("s1", "n1")
        self.assertEqual(ctx.register("我妈在开会", {}), 0)

    def test_roundtrip_verify_true(self):
        ctx = SessionContext("s1", "n1")
        ctx.register("我妈在开会", M)
        obf = obfuscate("我妈在开会", M)
        self.assertTrue(ctx.verify(obf, M))
        self.assertTrue(ctx.verify("今天 User_Kinship_Mother 开了会", M))

    def test_verify_true_when_no_manifest(self):
        ctx = SessionContext("s1", "n1")
        # 未登记任何占位符 -> 无从校验 -> True（空映射直通语义）
        self.assertTrue(ctx.verify("妈妈在开会", M))


class SessionMismatchTest(unittest.TestCase):
    def test_placeholder_rewritten_verify_false_and_logged(self):
        ctx = SessionContext("s1", "n1")
        ctx.register("我妈在开会", M)
        # 云端把占位符改写成了「妈妈」-> 占位符缺失 -> verify False
        self.assertFalse(ctx.verify("妈妈在开会", M))
        # 且必须留痕（结构化日志，不静默）
        self.assertTrue(ctx._mismatch_log, "verify 失败应写结构化日志")
        rec = ctx._mismatch_log[-1]
        self.assertEqual(rec["event"], "gateway.restore_mismatch")
        self.assertEqual(rec["session_id"], "s1")
        self.assertEqual(rec["missing_placeholders"], ["User_Kinship_Mother"])
        # M2：日志不得泄露真实实体（不含 mapping 字段、不含真名文本）
        self.assertNotIn("mapping", rec)
        self.assertNotIn("我妈", json.dumps(rec, ensure_ascii=False))

    def test_placeholder_dropped_verify_false_and_logged(self):
        ctx = SessionContext("s1", "n1")
        ctx.register("我妈在开会", M)
        self.assertFalse(ctx.verify("今天天气不错", M))
        self.assertTrue(ctx._mismatch_log)

    def test_log_mismatch_is_structured_json(self):
        import json
        ctx = SessionContext("s1", "n1")
        ctx.register("我妈在开会", M)
        ctx.verify("妈妈在开会", M)
        # 每条记录都能被 json.loads，证明是结构化、非静默
        for rec in ctx._mismatch_log:
            json.dumps(rec, ensure_ascii=False)  # 不应抛错


class SessionIsolationTest(unittest.TestCase):
    def test_two_sessions_do_not_interfere(self):
        a = SessionContext("A", "na")
        b = SessionContext("B", "nb")
        a.register("我妈在开会", M)
        # A 登记了占位符；B 未登记 -> B 校验通过，不受 A 影响
        self.assertTrue(b.verify("妈妈在开会", M))
        # A 自己的占位符被改写 -> False
        self.assertFalse(a.verify("妈妈在开会", M))

    def test_register_same_session_each_counts(self):
        ctx = SessionContext("s", "n")
        m2 = {"我妈": "User_Kinship_Mother", "张总": "User_Leader_Alpha"}
        c1 = ctx.register("我妈在开会", m2)
        c2 = ctx.register("张总在开会", m2)
        self.assertEqual(c1, 1)
        self.assertEqual(c2, 1)
        # 两次登记的占位符都被记录，回程校验需要在返回文本中同时出现
        self.assertTrue(ctx.verify("User_Kinship_Mother 和 User_Leader_Alpha 都来了", m2))


class LoadComplianceForTest(unittest.TestCase):
    def test_load_compliance_for_returns_dict(self):
        cfg = load_compliance_for()
        self.assertIsInstance(cfg, dict)
        self.assertIn("compliance_gateway", cfg)





class OpenSendIsolationTest(unittest.TestCase):
    def test_rounds_isolated(self):
        ctx = SessionContext("s", "n")
        ctx.open_send("r1")
        ctx.register("我妈在开会", M)
        self.assertTrue(ctx.verify("User_Kinship_Mother在开会", M))
        self.assertFalse(ctx.verify("张总来了", M))  # 本轮缺 Mother
        ctx.open_send("r2")
        ctx.register("张总在开会", {"张总": "User_Leader_Alpha"})
        # 本轮只校验 Leader，不再受上一轮 Mother 影响（M1）
        self.assertTrue(ctx.verify("User_Leader_Alpha在开会", {"张总": "User_Leader_Alpha"}))


if __name__ == "__main__":
    unittest.main()
