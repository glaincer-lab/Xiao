"""backend/errors.py 单测：异常 / 错误文本 → 一句人话的映射（E2c）。"""

from __future__ import annotations

import asyncio
import unittest

import httpx

from backend.provider_test import _human_reason  # 委托关系一并验证
from backend.errors import DEFAULT_REASON, human_reason, reason_from_text


def _status_err(cls, status: int) -> Exception:
    """构造带 status_code 的 openai 风格异常（不真发请求）。"""
    req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    resp = httpx.Response(status, request=req)
    return cls("err", response=resp, body=None)


class TestHumanReason(unittest.TestCase):
    def test_async_timeout(self):
        self.assertIn("超时", human_reason(asyncio.TimeoutError()))

    def test_builtin_timeout(self):
        self.assertIn("超时", human_reason(TimeoutError()))

    def test_401(self):
        import openai

        self.assertIn("密钥", human_reason(_status_err(openai.AuthenticationError, 401)))

    def test_403(self):
        import openai

        self.assertIn("密钥", human_reason(_status_err(openai.PermissionDeniedError, 403)))

    def test_404(self):
        import openai

        self.assertIn("模型", human_reason(_status_err(openai.NotFoundError, 404)))

    def test_429(self):
        import openai

        msg = human_reason(_status_err(openai.RateLimitError, 429))
        self.assertIn("429", msg)
        self.assertIn("额度", msg)

    def test_400(self):
        import openai

        self.assertIn("模型名", human_reason(_status_err(openai.BadRequestError, 400)))

    def test_5xx(self):
        import openai

        self.assertIn("5xx", human_reason(_status_err(openai.InternalServerError, 503)))

    def test_conn_error(self):
        import openai

        req = httpx.Request("POST", "https://api.example.com")
        self.assertIn("连接", human_reason(openai.APIConnectionError(request=req)))

    def test_unknown_returns_custom_default_verbatim(self):
        e = RuntimeError("boom")
        self.assertEqual(human_reason(e, default="我的默认话术"), "我的默认话术")

    def test_unknown_returns_module_default(self):
        self.assertEqual(human_reason(RuntimeError("boom")), DEFAULT_REASON)


class TestReasonFromText(unittest.TestCase):
    def test_empty_returns_default(self):
        self.assertEqual(reason_from_text(""), "未知错误")
        self.assertEqual(reason_from_text(None, default="没有错误"), "没有错误")

    def test_401_keyword(self):
        self.assertIn("密钥", reason_from_text("Error code: 401 - Invalid API Key"))

    def test_quota_keywords(self):
        self.assertIn("额度", reason_from_text("Throttling: Requests rate exceeded"))
        self.assertIn("额度", reason_from_text("Arrearage: account balance exhausted"))
        self.assertIn("额度", reason_from_text("欠费请充值"))

    def test_timeout_keyword(self):
        self.assertIn("超时", reason_from_text("Request timed out after 10000ms"))

    def test_404_keyword(self):
        self.assertIn("不存在", reason_from_text("Error code: 404 - model not exist"))

    def test_path_keyword(self):
        self.assertIn("不存在", reason_from_text("[Errno 2] No such file or directory: 'x.onnx'"))

    def test_conn_keyword(self):
        self.assertIn("连接", reason_from_text("[WinError 10061] connection refused"))

    def test_key_beats_quota_when_both_present(self):
        self.assertIn("密钥", reason_from_text("401 with rate limit details"))

    def test_unrelated_short_text_verbatim(self):
        self.assertEqual(reason_from_text("weird failure"), "weird failure")

    def test_long_text_truncated(self):
        raw = "x" * 200
        out = reason_from_text(raw)
        self.assertLessEqual(len(out), 80)
        self.assertTrue(out.endswith("…"))


class TestProviderTestDelegation(unittest.TestCase):
    """E2b 的 _human_reason 已改为委托 errors.human_reason，行为不变。"""

    def test_unknown_keeps_legacy_shape(self):
        self.assertEqual(_human_reason(RuntimeError("boom")), "测试失败：boom")

    def test_status_phrase_unchanged(self):
        import openai

        self.assertIn("密钥", _human_reason(_status_err(openai.AuthenticationError, 401)))
        self.assertIn("额度", _human_reason(_status_err(openai.RateLimitError, 429)))


if __name__ == "__main__":
    unittest.main()
