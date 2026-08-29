"""E4 健康状态灯的回归测试（不发真实外网请求）。

重点防住：
- resolve_active：从 config 里挑当前激活方案（命中 active / 缺 active / 脏数据各得其所）
- probe_component：包装 test_provider 补齐状态灯字段（label/scheme/ok/msg/latency 不丢）
- agent 项在人话提示里给安装引导（不抛堆栈）

运行（同 tests/test_core.py，需先装后端依赖）：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from backend import provider_test as pt


def _cfg(target: str, block: dict) -> dict:
    return {target: block}


class TestResolveActive(unittest.TestCase):
    def test_picks_active_scheme(self):
        cfg = _cfg(
            "llm",
            {
                "active": "b",
                "models": [
                    {"id": "a", "name": "方案A", "apiKey": "k1"},
                    {"id": "b", "name": "方案B", "apiKey": "k2"},
                ],
            },
        )
        m, scheme = pt.resolve_active(cfg, "llm")
        self.assertEqual(m.get("id"), "b")
        self.assertEqual(scheme, "方案B")

    def test_missing_active_returns_empty(self):
        cfg = _cfg("tts", {"models": [{"id": "x", "name": "X"}]})
        m, scheme = pt.resolve_active(cfg, "tts")
        self.assertEqual(m, {})
        self.assertEqual(scheme, "")

    def test_dirty_config_is_safe(self):
        for cfg in ({}, {"asr": None}, {"asr": {"models": "oops", "active": 3}}, {"asr": {"models": [1, "x"]}}):
            m, scheme = pt.resolve_active(cfg, "asr")
            self.assertEqual(m, {})
            self.assertEqual(scheme, "")

    def test_active_without_name_falls_back_to_id(self):
        cfg = _cfg("asr", {"active": "asr_1", "models": [{"id": "asr_1"}]})
        m, scheme = pt.resolve_active(cfg, "asr")
        self.assertEqual(m.get("id"), "asr_1")
        self.assertEqual(scheme, "asr_1")


class TestProbeComponent(unittest.TestCase):
    def test_fields_filled_from_test_provider(self):
        fake = {"ok": True, "msg": "连通正常", "latency_ms": 42}
        with mock.patch.object(pt, "test_provider", mock.AsyncMock(return_value=fake)):
            r = asyncio.run(pt.probe_component("llm", {"provider": "deepseek"}, "DeepSeek"))
        self.assertEqual(r["key"], "llm")
        self.assertEqual(r["label"], "大脑（LLM）")
        self.assertEqual(r["scheme"], "DeepSeek")
        self.assertTrue(r["ok"])
        self.assertEqual(r["msg"], "连通正常")
        self.assertEqual(r["latency_ms"], 42)

    def test_failed_probe_keeps_human_message(self):
        fake = {"ok": False, "msg": "尚未填写 DeepSeek 的 API Key：请到服务商控制台创建后粘贴到设置里", "latency_ms": 3}
        with mock.patch.object(pt, "test_provider", mock.AsyncMock(return_value=fake)):
            r = asyncio.run(pt.probe_component("asr", {}, ""))
        self.assertFalse(r["ok"])
        self.assertIn("API Key", r["msg"])
        self.assertEqual(r["scheme"], "默认方案")
        self.assertEqual(r["label"], "语音识别（ASR）")

    def test_labels_cover_three_targets(self):
        for key, expect in (("asr", "语音识别（ASR）"), ("llm", "大脑（LLM）"), ("tts", "语音合成（TTS）")):
            with mock.patch.object(pt, "test_provider", mock.AsyncMock(return_value={"ok": True, "msg": ""})):
                r = asyncio.run(pt.probe_component(key, {}))
            self.assertEqual(r["label"], expect)


class TestAgentItem(unittest.TestCase):
    def test_green_light_when_available(self):
        r = pt.agent_item(True)
        self.assertTrue(r["ok"])
        self.assertIn("已找到 dsh 命令", r["msg"])
        self.assertEqual(r["key"], "agent")

    def test_red_light_gives_install_hint(self):
        r = pt.agent_item(False)
        self.assertFalse(r["ok"])
        self.assertIn("没找到 dsh 命令", r["msg"])
        self.assertIn("安装 DSH", r["msg"])
        self.assertNotIn("Traceback", r["msg"])


if __name__ == "__main__":
    unittest.main()
