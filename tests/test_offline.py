"""端侧离线链路自检（v3）的回归测试：纯配置判定，零网络。

重点防住：
- 四环节本地引擎判定（方案内 provider/engine 字段，wake 用 engine）
- 方案缺失时回退旧单字段配置（wake_word.engine / asr.provider / llm.provider）
- omni 一体化计为本地（服务也在本机）；未知引擎按云端处理（宁报不可用）
- offline_item 与健康灯字段对齐（key/label/scheme/ok/msg/latency_ms）

运行（同 tests/test_core.py，需先装后端依赖）：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from backend.offline import check_offline, offline_item


def _cfg(key: str, engine: str, field: str = "provider") -> dict:
    return {key: {"active": "a", "models": [{"id": "a", "name": "A", field: engine}]}}


def _all_local() -> dict:
    cfg = {}
    cfg.update(_cfg("wake_word", "sherpa", "engine"))
    cfg.update(_cfg("asr", "local"))
    cfg.update(_cfg("llm", "ollama"))
    cfg.update(_cfg("tts", "piper"))
    return cfg


class TestCheckOffline(unittest.TestCase):
    def test_all_local_is_ready(self):
        r = check_offline(_all_local())
        self.assertTrue(r["ready"])
        self.assertTrue(all(i["local"] for i in r["items"]))
        self.assertIn("断网", r["msg"])
        self.assertNotIn("Traceback", r["msg"])

    def test_omni_family_counts_as_local(self):
        cfg = {}
        cfg.update(_cfg("wake_word", "omni", "engine"))
        cfg.update(_cfg("asr", "omni"))
        cfg.update(_cfg("llm", "omni"))
        cfg.update(_cfg("tts", "omni"))
        r = check_offline(cfg)
        self.assertTrue(r["ready"])

    def test_default_cloud_shape_not_ready(self):
        cfg = {}
        cfg.update(_cfg("wake_word", "sherpa", "engine"))
        cfg.update(_cfg("asr", "cloud"))
        cfg.update(_cfg("llm", "deepseek"))
        cfg.update(_cfg("tts", "qwen_rt"))
        r = check_offline(cfg)
        self.assertFalse(r["ready"])
        bad = [i["label"] for i in r["items"] if not i["local"]]
        self.assertEqual(bad, ["识别", "大脑（LLM）", "播报"])
        self.assertIn("设置", r["msg"])

    def test_legacy_fields_fallback(self):
        cfg = {
            "wake_word": {"engine": "sherpa"},
            "asr": {"provider": "local"},
            "llm": {"provider": "local"},
            "tts": {"provider": "piper"},
        }
        r = check_offline(cfg)
        self.assertTrue(r["ready"])

    def test_llm_legacy_cloud_provider_omni(self):
        cfg = {
            "wake_word": {"engine": "sherpa"},
            "asr": {"provider": "local"},
            "llm": {"cloud": {"provider": "omni"}},
            "tts": {"provider": "piper"},
        }
        r = check_offline(cfg)
        self.assertTrue(r["ready"])

    def test_unknown_engine_treated_as_cloud(self):
        cfg = _all_local()
        cfg.update(_cfg("llm", "some-new-thing"))
        r = check_offline(cfg)
        self.assertFalse(r["ready"])

    def test_empty_config_not_ready_and_safe(self):
        r = check_offline({})
        self.assertFalse(r["ready"])
        self.assertEqual(len(r["items"]), 4)
        self.assertNotIn("Traceback", r["msg"])

    def test_omni_items_hint_local_service(self):
        cfg = _all_local()
        cfg.update(_cfg("llm", "omni"))
        r = check_offline(cfg)
        llm_item = next(i for i in r["items"] if i["key"] == "llm")
        self.assertTrue(llm_item["local"])
        self.assertIn("vLLM-omni", llm_item["msg"])


class TestOfflineItem(unittest.TestCase):
    def test_item_shape_matches_health_lamp(self):
        r = offline_item(_all_local())
        self.assertEqual(set(r), {"key", "label", "scheme", "ok", "msg", "latency_ms"})
        self.assertEqual(r["key"], "offline")
        self.assertTrue(r["ok"])
        self.assertEqual(r["latency_ms"], 0)

    def test_red_light_names_missing_parts(self):
        r = offline_item(_cfg("llm", "deepseek"))
        self.assertFalse(r["ok"])
        self.assertIn("大脑（LLM）", r["msg"])
        self.assertNotIn("Traceback", r["msg"])


if __name__ == "__main__":
    unittest.main()
