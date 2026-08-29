"""设置注册表 + 引擎默认常量 的回归测试。

重点防住：
- 注册表 dict 缺逗号 / 语法错误（历史上 P1-1 曾因 llm.timeout_sec 后缺逗号导致整个 schema 无法导入）
- show_if 指向不存在的 path（前后端字段错位）
- 引擎默认常量被改坏或与前缀提示漂移（P2-3 收敛后应只有 config.py 一个来源）

运行（同 tests/test_core.py，需先装后端依赖）：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from backend.config import (
    LLM_CLOUD_DEFAULTS,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OMNI_BASE_URL,
    OMNI_MODEL,
)
from backend.settings_schema import SCHEMA


class TestDefaults(unittest.TestCase):
    def test_omni_defaults(self):
        self.assertEqual(OMNI_BASE_URL, "http://localhost:8000/v1")
        self.assertEqual(OMNI_MODEL, "openbmb/MiniCPM-o-4_5")

    def test_ollama_defaults(self):
        self.assertEqual(OLLAMA_BASE_URL, "http://localhost:11434/v1")
        self.assertEqual(OLLAMA_MODEL, "qwen2.5:7b")

    def test_cloud_defaults_map(self):
        self.assertIn("deepseek", LLM_CLOUD_DEFAULTS)
        self.assertEqual(LLM_CLOUD_DEFAULTS["deepseek"][1], "deepseek-v4-pro")
        self.assertEqual(LLM_CLOUD_DEFAULTS["openai"][0], "https://api.openai.com/v1")
        self.assertEqual(LLM_CLOUD_DEFAULTS["glm"][2], None)


class TestSchema(unittest.TestCase):
    def test_paths_unique(self):
        paths = [f["path"] for f in SCHEMA]
        self.assertEqual(len(paths), len(set(paths)), "注册表存在重复 path")

    def test_llm_provider_has_omni(self):
        field = next(f for f in SCHEMA if f["path"] == "llm.provider")
        values = {o["value"] for o in field["options"]}
        self.assertIn("omni", values)

    def test_llm_timeout_sec_exists(self):
        field = next(f for f in SCHEMA if f["path"] == "llm.timeout_sec")
        self.assertEqual(field["type"], "number")
        self.assertEqual(field["min"], 5)

    def test_show_if_refers_to_existing_path(self):
        paths = {f["path"] for f in SCHEMA}
        for f in SCHEMA:
            show_if = f.get("show_if")
            if show_if:
                self.assertIn(show_if["path"], paths, f"{f['path']} 的 show_if 指向不存在字段")

    def test_select_options_wellformed(self):
        # 「选择器」字段（供应商/引擎/模型/档位）必须带 status，用于前端加状态徽标；
        # 纯枚举（音色/语速/路由模式）只有 value/label，不强求 status。
        status_suffixes = ("provider", "engine", "tier", "model")
        for f in SCHEMA:
            if f.get("type") != "select":
                continue
            opts = f["options"]
            self.assertTrue(opts, f"{f['path']} 是空 options")
            for o in opts:
                self.assertIn("value", o)
                self.assertIn("label", o)
            # 一旦某选项带 status，则该字段所有选项都应带，且为合法枚举
            if any("status" in o for o in opts):
                for o in opts:
                    self.assertIn("status", o)
                    self.assertIn(o["status"], ("ok", "optional", "beta", "paid"))
            # 选择器字段必须每项都有 status
            if f["path"].split(".")[-1] in status_suffixes:
                for o in opts:
                    self.assertIn("status", o, f"{f['path']} 的选项缺 status")


if __name__ == "__main__":
    unittest.main()
