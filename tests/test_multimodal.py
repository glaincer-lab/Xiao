"""多模态输入单测：ChatMessage vision 序列化 / agent 图片透传 / core 图片开关与分流。"""
from __future__ import annotations

import asyncio
import threading
import time
import unittest
from collections import deque
from unittest import mock

from backend.config import Config
from backend.llm.base import ChatMessage, Completion
from backend.rules import RuleEngine
from backend.session.state import State


def _img(tag: str = "a") -> str:
    return f"data:image/png;base64,{tag}"


class TestChatMessageVision(unittest.TestCase):
    """to_dict 序列化：纯文本不变；带图 user 消息转 OpenAI vision parts 格式。"""

    def test_plain_text_unchanged(self):
        d = ChatMessage(role="user", content="你好").to_dict()
        self.assertEqual(d, {"role": "user", "content": "你好"})

    def test_images_become_parts(self):
        m = ChatMessage(role="user", content="看图", images=[_img("a"), _img("b")])
        d = m.to_dict()
        self.assertIsInstance(d["content"], list)
        self.assertEqual(d["content"][0], {"type": "text", "text": "看图"})
        self.assertEqual(d["content"][1], {"type": "image_url", "image_url": {"url": _img("a")}})
        self.assertEqual(len(d["content"]), 3)

    def test_invalid_dropped_and_capped(self):
        images = ["http://x/a.png", "text/html", None] + [_img(i) for i in "abcde"]
        d = ChatMessage(role="user", content="x", images=images).to_dict()
        self.assertEqual(len(d["content"]), 5)  # 1 文本 + 最多 4 图
        urls = [p["image_url"]["url"] for p in d["content"][1:]]
        self.assertEqual(urls, [_img(i) for i in "abcd"])

    def test_assistant_role_keeps_string(self):
        m = ChatMessage(role="assistant", content="ok", images=[_img("a")])
        self.assertEqual(m.to_dict()["content"], "ok")

    def test_images_key_never_leaks(self):
        d = ChatMessage(role="user", content="hi", images=[_img("a")]).to_dict()
        self.assertNotIn("images", d)


class _FakeLLM:
    def __init__(self, reply: str = "看到了"):
        self.reply = reply
        self.calls: list[list[ChatMessage]] = []

    async def complete(self, messages, tools=None):
        self.calls.append(list(messages))
        return Completion(content=self.reply, tool_calls=[])


class _FakeTTS:
    def __init__(self):
        self.spoken: list[str] = []

    async def speak(self, text):
        self.spoken.append(text)


class _FakeRegistry:
    def schemas(self):
        return []

    async def call(self, name, **kwargs):
        return "ok"


class TestAgentImages(unittest.TestCase):
    def _agent(self):
        from backend.agent import Agent

        llm = _FakeLLM()
        agent = Agent(llm=llm, tts=_FakeTTS(), registry=_FakeRegistry())
        return agent, llm

    def test_handle_forwards_images(self):
        agent, llm = self._agent()
        asyncio.run(agent.handle("看图", [_img("a")]))
        last = llm.calls[0][-1]
        self.assertEqual(last.role, "user")
        self.assertEqual(last.images, [_img("a")])
        self.assertIsInstance(last.to_dict()["content"], list)

    def test_handle_without_images_stays_plain(self):
        agent, llm = self._agent()
        asyncio.run(agent.handle("在吗"))
        last = llm.calls[0][-1]
        self.assertIsNone(last.images)
        self.assertEqual(last.to_dict()["content"], "在吗")


class _FakeAgent:
    def __init__(self):
        self.calls: list[dict] = []

    async def handle(self, text, images=None):
        self.calls.append({"text": text, "images": images})


class TestPipelineImages(unittest.TestCase):
    """core 分流：开关关闭→语音提示不进 agent；开启→强制走 chat 且清洗透传。"""

    def _make_pipeline(self):
        from backend.core import Pipeline

        p = Pipeline.__new__(Pipeline)
        p._state = State.IDLE
        p._lock = threading.Lock()
        p._tasks = None
        p._loop = None
        p._agent = None
        p._tts = None
        p._in_utterance = False
        p._pre_roll = deque()
        p._last_active = 0.0
        p._clear_enabled = False
        p._dismiss_enabled = False
        p._shutdown_enabled = False
        p._rules = RuleEngine()
        p._router = None
        return p

    def _patch_core(self, enabled: bool):
        import backend.core as core_mod

        old_cfg = core_mod.config
        core_mod.config = Config({"llm": {"cloud": {"image_input": enabled}}})
        self.addCleanup(lambda: setattr(core_mod, "config", old_cfg))
        # cloud_vision 授权默认关会拦截图片；图片转发测试需授权
        auth_patcher = mock.patch.object(core_mod.AuthorizationCenter, "is_granted", return_value=True)
        auth_patcher.start()
        self.addCleanup(auth_patcher.stop)
        return core_mod

    def test_disabled_shows_notice_not_agent(self):
        core_mod = self._patch_core(enabled=False)
        p = self._make_pipeline()
        fake = _FakeAgent()
        p._agent = fake
        with mock.patch.object(core_mod, "emit") as em:
            p.submit_text("看图", [_img("a")])
        self.assertEqual(fake.calls, [])
        results = [c for c in em.call_args_list if c.args and c.args[0] == "assistant_result"]
        self.assertEqual(len(results), 1)
        self.assertIn("支持图片输入", results[0].kwargs["text"])

    def test_enabled_forwards_to_agent(self):
        core_mod = self._patch_core(enabled=True)
        p = self._make_pipeline()
        fake = _FakeAgent()
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
        self.addCleanup(loop.call_soon_threadsafe, loop.stop)
        p._loop = loop
        p._agent = fake
        p.submit_text("看看这张图", [_img("a")])
        for _ in range(200):
            if fake.calls:
                break
            time.sleep(0.01)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["text"], "看看这张图")
        self.assertEqual(fake.calls[0]["images"], [_img("a")])

    def test_enabled_empty_text_uses_placeholder(self):
        core_mod = self._patch_core(enabled=True)
        p = self._make_pipeline()
        fake = _FakeAgent()
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
        self.addCleanup(loop.call_soon_threadsafe, loop.stop)
        p._loop = loop
        p._agent = fake
        p.submit_text("", [_img("z")])
        for _ in range(200):
            if fake.calls:
                break
            time.sleep(0.01)
        self.assertEqual(fake.calls[0]["text"], "请看这张图片")
        self.assertEqual(fake.calls[0]["images"], [_img("z")])

    def test_invalid_only_falls_back_to_text(self):
        core_mod = self._patch_core(enabled=True)
        p = self._make_pipeline()
        fake = _FakeAgent()
        p._agent = fake
        with mock.patch.object(core_mod, "emit") as em:
            p.submit_text("今天天气怎么样", ["http://x/a.png"])
        self.assertEqual(fake.calls, [])  # 无可用 loop，文本走普通链路
        blocked = [c for c in em.call_args_list if c.args and c.args[0] == "assistant_result"]
        self.assertEqual(blocked, [])

    def test_empty_submit_is_noop(self):
        core_mod = self._patch_core(enabled=True)
        p = self._make_pipeline()
        fake = _FakeAgent()
        p._agent = fake
        with mock.patch.object(core_mod, "emit") as em:
            p.submit_text("", None)
        self.assertEqual(fake.calls, [])
        self.assertEqual(em.call_args_list, [])


if __name__ == "__main__":
    unittest.main()
