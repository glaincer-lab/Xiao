"""长期记忆（v3）：MemoryStore 落盘 / remember 工具 / Agent 新会话注入。"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from backend.agent import Agent, system_prompt
from backend.memory import MemoryStore, memory_store
from backend.tools.remember import RememberTool


class _FakeStore:
    def __init__(self) -> None:
        self.saved: list[str] = []

    def add(self, text: str, source: str = "user") -> dict:
        self.saved.append(text)
        return {"id": "x1", "text": text, "ts": 0.0, "source": source}

    def entries(self) -> list[dict]:
        return [
            {"id": f"x{i}", "text": t, "ts": 0.0, "source": "user"}
            for i, t in enumerate(self.saved, 1)
        ]


class _FakeMemory:
    def __init__(self, text: str) -> None:
        self._text = text

    def context_text(self) -> str:
        return self._text


class TestMemoryStore(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "memory.json"

    def test_add_and_reload_roundtrip(self):
        s = MemoryStore(path=self.path, max_entries=10)
        s.add("用户偏好简短回答")
        s.add("成果：爬虫脚本在 logs/output")
        s2 = MemoryStore(path=self.path, max_entries=10)
        self.assertEqual(
            [e["text"] for e in s2.entries()],
            ["用户偏好简短回答", "成果：爬虫脚本在 logs/output"],
        )

    def test_fifo_trim(self):
        s = MemoryStore(path=self.path, max_entries=3)
        for i in range(5):
            s.add(f"第{i}条")
        self.assertEqual([e["text"] for e in s.entries()], ["第2条", "第3条", "第4条"])

    def test_corrupt_json_resets(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{不是json", encoding="utf-8")
        s = MemoryStore(path=self.path, max_entries=10)
        self.assertEqual(s.entries(), [])
        s.add("损坏后仍可写入")
        s2 = MemoryStore(path=self.path, max_entries=10)
        self.assertEqual(len(s2.entries()), 1)

    def test_clear_persists(self):
        s = MemoryStore(path=self.path, max_entries=10)
        s.add("会被清掉")
        s.clear()
        self.assertEqual(s.entries(), [])
        self.assertEqual(MemoryStore(path=self.path, max_entries=10).entries(), [])

    def test_context_text_empty_and_content(self):
        s = MemoryStore(path=self.path, max_entries=10)
        self.assertEqual(s.context_text(), "")
        s.add("记住我喜欢简短回答")
        self.assertIn("简短回答", s.context_text())

    def test_context_text_limit(self):
        s = MemoryStore(path=self.path, max_entries=10)
        for i in range(5):
            s.add(f"记忆{i}")
        text = s.context_text(limit=2)
        self.assertIn("记忆4", text)
        self.assertIn("记忆3", text)
        self.assertNotIn("记忆2", text)

    def test_entries_are_copies(self):
        s = MemoryStore(path=self.path, max_entries=10)
        s.add("原样")
        got = s.entries()
        got[0]["text"] = "被改了"
        self.assertEqual(s.entries()[0]["text"], "原样")

    def test_add_empty_raises(self):
        s = MemoryStore(path=self.path, max_entries=10)
        with self.assertRaises(ValueError):
            s.add("   ")


class TestRememberTool(unittest.TestCase):
    def test_run_saves_to_store(self):
        fake = _FakeStore()
        r = asyncio.run(RememberTool(store=fake).run(text="用户生日是3月8日"))
        self.assertEqual(fake.saved, ["用户生日是3月8日"])
        self.assertIn("已记住", r)

    def test_run_empty_text(self):
        fake = _FakeStore()
        r = asyncio.run(RememberTool(store=fake).run(text="  "))
        self.assertEqual(fake.saved, [])
        self.assertIn("记什么", r)

    def test_run_missing_text_key(self):
        fake = _FakeStore()
        r = asyncio.run(RememberTool(store=fake).run())
        self.assertEqual(fake.saved, [])
        self.assertIn("记什么", r)

    def test_schema_targets_explicit_requests(self):
        self.assertIn("明确", RememberTool.description)
        self.assertEqual(RememberTool.parameters["required"], ["text"])


class TestAgentInjection(unittest.TestCase):
    def test_memory_injected_into_system_message(self):
        agent = Agent(None, None, None, memory=_FakeMemory("1. 用户偏好简短回答"))
        msgs = agent._messages()
        self.assertEqual(msgs[0].role, "system")
        self.assertIn("用户偏好简短回答", msgs[0].content)

    def test_empty_memory_keeps_system_prompt_clean(self):
        agent = Agent(None, None, None, memory=_FakeMemory(""))
        self.assertEqual(agent._messages()[0].content, system_prompt())

    def test_default_memory_is_singleton(self):
        agent = Agent(None, None, None)
        self.assertIs(agent._memory, memory_store)


if __name__ == "__main__":
    unittest.main()
