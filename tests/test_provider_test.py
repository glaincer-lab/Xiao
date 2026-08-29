"""E2b 连通性测试接口的回归测试（不发真实外网请求）。

重点防住：
- 异常 → 人话映射不被改坏（401/429/404/400/超时/未知异常各有一句话，不出现堆栈）
- 分发分支：未知 target / 未支持的 LLM 服务商 / 缺 Key 的前置拦截（不发网络请求）
- TTS 本地方案走 preflight 自检（缺声库文件给人话），验证一次性引擎的构建路径

运行（同 tests/test_core.py，需先装后端依赖）：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

import httpx
import openai

from backend import provider_test as pt


def _status_err(cls, status: int) -> Exception:
    """构造带 status_code 的 openai 状态类异常（无需真实网络）。"""
    req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    resp = httpx.Response(status, request=req)
    return cls("err", response=resp, body=None)


class TestHumanReason(unittest.TestCase):
    def test_401_maps_to_key_hint(self):
        msg = pt._human_reason(_status_err(openai.AuthenticationError, 401))
        self.assertIn("密钥", msg)

    def test_429_maps_to_quota_hint(self):
        msg = pt._human_reason(_status_err(openai.RateLimitError, 429))
        self.assertIn("429", msg)
        self.assertIn("额度", msg)

    def test_404_maps_to_model_hint(self):
        msg = pt._human_reason(_status_err(openai.NotFoundError, 404))
        self.assertIn("模型", msg)

    def test_400_maps_to_model_name_hint(self):
        msg = pt._human_reason(_status_err(openai.BadRequestError, 400))
        self.assertIn("模型名", msg)

    def test_5xx_maps_to_server_hint(self):
        msg = pt._human_reason(_status_err(openai.InternalServerError, 503))
        self.assertIn("5xx", msg)

    def test_timeout_maps_to_network_hint(self):
        msg = pt._human_reason(asyncio.TimeoutError())
        self.assertIn("超时", msg)

    def test_generic_exception_keeps_reason_readable(self):
        msg = pt._human_reason(RuntimeError("boom"))
        self.assertTrue(msg.startswith("测试失败"))
        self.assertIn("boom", msg)


class TestDispatch(unittest.TestCase):
    def test_unknown_target(self):
        r = asyncio.run(pt.test_provider("video", {}))
        self.assertFalse(r["ok"])
        self.assertIn("target", r["msg"])

    def test_unknown_llm_provider(self):
        r = asyncio.run(pt.test_provider("llm", {"provider": "nope"}))
        self.assertFalse(r["ok"])
        self.assertIn("未支持", r["msg"])

    def test_llm_cloud_missing_key_short_circuits(self):
        with mock.patch.object(pt, "env", lambda k: None):
            r = asyncio.run(
                pt.test_provider("llm", {"provider": "deepseek", "model": "deepseek-v4-pro"})
            )
        self.assertFalse(r["ok"])
        self.assertIn("API Key", r["msg"])
        self.assertIn("latency_ms", r)

    def test_asr_cloud_missing_key_short_circuits(self):
        with mock.patch.object(pt, "env", lambda k: None):
            r = asyncio.run(
                pt.test_provider(
                    "asr",
                    {"provider": "cloud", "model": "qwen-audio-3.0-asr-flash-streaming"},
                )
            )
        self.assertFalse(r["ok"])
        self.assertIn("DashScope", r["msg"])

    def test_tts_piper_missing_model_preflight(self):
        r = asyncio.run(
            pt.test_provider("tts", {"provider": "piper", "piperModel": "models/__no_such__.onnx"})
        )
        self.assertFalse(r["ok"])
        self.assertIn("声库", r["msg"])

    def test_latency_always_present(self):
        r = asyncio.run(pt.test_provider("bogus", None))
        self.assertIn("latency_ms", r)


if __name__ == "__main__":
    unittest.main()
