"""最小冒烟/单测集：状态机 / 路由 / 审批词表 / config 校验 + app 导入冒烟。

运行（标准库 unittest）：
    python -m unittest discover -s tests -v
也可用 pytest 直接跑（pytest tests/）。

依赖说明：虽用标准库 unittest 作 runner，但被测模块链需要后端依赖——
backend.config 需要 pyyaml；test_app_imports 导入 backend.main 还需要
fastapi / uvicorn 及 main.py 顶层 import 的各模块。缺依赖时先：
    pip install -r requirements.txt
"""
from __future__ import annotations

import unittest
from unittest import mock

from backend.config import OMNI_MODEL, Config
from backend.llm.base import Completion
from backend.perms import Perms
from backend.router import Router
from backend.session.state import EventBus, State


class TestEventBus(unittest.TestCase):
    def test_subscribe_and_unsubscribe(self):
        bus = EventBus()
        got: list = []
        unsub = bus.subscribe(lambda e: got.append(e["x"]))
        bus.emit({"x": 1})
        self.assertEqual(got, [1])
        unsub()
        bus.emit({"x": 2})
        self.assertEqual(got, [1])

    def test_exception_isolated(self):
        bus = EventBus()
        got: list = []

        def boom(_e):
            raise RuntimeError("boom")

        bus.subscribe(boom)
        bus.subscribe(lambda _e: got.append("ok"))
        bus.emit({})  # 不抛异常
        self.assertEqual(got, ["ok"])


class TestState(unittest.TestCase):
    def test_known_states(self):
        self.assertEqual(State.WORKING.value, "working")
        self.assertEqual(State.AWAIT_APPROVAL.value, "await_approval")
        self.assertEqual(len(State), 9)


class TestConfig(unittest.TestCase):
    def test_get_dotted(self):
        c = Config({"a": {"b": 2}})
        self.assertEqual(c.get("a.b"), 2)
        self.assertEqual(c.get("a.missing", 9), 9)

    def test_section(self):
        c = Config({"a": {"b": 2}, "n": 3})
        self.assertEqual(c.section("a"), {"b": 2})
        self.assertEqual(c.section("n"), {})


class TestRouter(unittest.TestCase):
    def test_auto_keyword_to_dsh(self):
        r = Router()
        r.set_mode("auto")
        self.assertEqual(r.route("帮我写代码实现一个爬虫"), "dsh")

    def test_auto_else_chat(self):
        r = Router()
        r.set_mode("auto")
        self.assertEqual(r.route("今天天气怎么样"), "chat")

    def test_forced_modes(self):
        r = Router()
        r.set_mode("chat")
        self.assertEqual(r.route("写代码"), "chat")
        r.set_mode("dsh")
        self.assertEqual(r.route("今天天气"), "dsh")


class TestPerms(unittest.TestCase):
    def test_predict_network(self):
        p = Perms()
        self.assertIn("network", p.predict("帮我联网搜索最新新闻"))

    def test_predict_install(self):
        p = Perms()
        self.assertIn("install", p.predict("pip 安装 requests"))

    def test_predict_no_hit(self):
        p = Perms()
        self.assertEqual(p.predict("你好呀"), set())


class TestSmoke(unittest.TestCase):
    def test_app_imports(self):
        """冒烟：FastAPI app 可构造，验证全模块导入无错、中间件/端点注册成功。"""
        from backend.main import app
        self.assertEqual(app.title, "Xiao Voice Assistant")


class TestLLMFactory(unittest.TestCase):
    """LLM 工厂回归：scheme 与旧字段两条路径都必须能构建 omni，不得抛 ValueError。"""

    def _patch(self, data):
        import backend.llm.factory as factory

        from backend.config import Config

        old = factory.config
        factory.config = Config(data)
        self.addCleanup(lambda: setattr(factory, "config", old))
        return factory

    def test_omni_scheme_builds_with_explicit_fields(self):
        factory = self._patch({
            "llm": {
                "models": [
                    {"id": "o1", "provider": "omni", "baseUrl": "http://127.0.0.1:8000/v1",
                     "model": "MiniCPM-o-4_5", "api_key": "k", "temperature": 0.4},
                ],
                "active": "o1",
            }
        })
        from backend.llm.openai_compat import OpenAICompatClient

        client = factory.build_llm()
        self.assertIsInstance(client, OpenAICompatClient)
        self.assertEqual(client._model, "MiniCPM-o-4_5")
        self.assertEqual(client._temperature, 0.4)

    def test_omni_scheme_falls_back_to_defaults(self):
        factory = self._patch({"llm": {"models": [{"id": "o1", "provider": "omni"}], "active": "o1"}})
        from backend.llm.openai_compat import OpenAICompatClient

        client = factory.build_llm()
        self.assertIsInstance(client, OpenAICompatClient)
        self.assertEqual(client._model, OMNI_MODEL)


class TestSampling(unittest.TestCase):
    """E3 采样参数规整：留空不发送、越界收敛、top_k 只对宽容供应商透传。"""

    def _patch(self, data):
        import backend.llm.factory as factory

        from backend.config import Config

        old = factory.config
        factory.config = Config(data)
        self.addCleanup(lambda: setattr(factory, "config", old))
        # cloud_llm 授权默认关会回退本地 Ollama；采样字段接线测试需授权云端
        auth_patcher = mock.patch("backend.authorization.AuthorizationCenter.is_granted", return_value=True)
        auth_patcher.start()
        self.addCleanup(auth_patcher.stop)
        return factory

    def test_empty_means_not_sent(self):
        from backend.llm.factory import _sampling

        self.assertEqual(_sampling("deepseek", "", None, ""), {})

    def test_top_p_clamped(self):
        from backend.llm.factory import _sampling

        self.assertEqual(_sampling("openai", "1.7", None, None), {"top_p": 1.0})
        self.assertEqual(_sampling("openai", "-3", None, None), {"top_p": 0.0})

    def test_topk_strict_provider_store_only(self):
        from backend.llm.factory import _sampling

        self.assertEqual(_sampling("deepseek", "0.9", "40", "8192"), {"top_p": 0.9, "max_tokens": 8192})
        self.assertEqual(_sampling("kimi", "0.9", "40", None), {"top_p": 0.9})

    def test_topk_tolerant_provider_passthrough(self):
        from backend.llm.factory import _sampling

        self.assertEqual(
            _sampling("dashscope", "0.9", "40", "8192"),
            {"top_p": 0.9, "max_tokens": 8192, "extra_body": {"top_k": 40}},
        )

    def test_invalid_ignored(self):
        from backend.llm.factory import _sampling

        self.assertEqual(_sampling("openai", "abc", "x", "y"), {})

    def test_scheme_fields_wired_deepseek(self):
        factory = self._patch({
            "llm": {"models": [
                {"id": "d1", "provider": "deepseek", "model": "m1",
                 "topP": "0.9", "topK": "40", "contextOutput": "8192"},
            ], "active": "d1"},
        })
        client = factory.build_llm()
        self.assertEqual(client._top_p, 0.9)
        self.assertEqual(client._max_tokens, 8192)
        self.assertIsNone(client._extra_body)  # deepseek 不透传 top_k

    def test_scheme_fields_wired_dashscope_topk(self):
        factory = self._patch({
            "llm": {"models": [
                {"id": "q1", "provider": "dashscope", "model": "m1", "topK": "40"},
            ], "active": "q1"},
        })
        client = factory.build_llm()
        self.assertEqual(client._extra_body, {"top_k": 40})


class TestToolRounds(unittest.TestCase):
    """E3 工具调用轮数：默认 500，配置可覆盖，非法值回落默认。"""

    def _patch_agent_config(self, data):
        import backend.agent as agent_mod

        from backend.config import Config

        old = agent_mod.config
        agent_mod.config = Config(data)
        self.addCleanup(lambda: setattr(agent_mod, "config", old))

    def test_default_500(self):
        self._patch_agent_config({})
        from backend.agent import DEFAULT_TOOL_ROUNDS, tool_rounds

        self.assertEqual(tool_rounds(), DEFAULT_TOOL_ROUNDS)
        self.assertEqual(DEFAULT_TOOL_ROUNDS, 500)

    def test_config_override(self):
        self._patch_agent_config({"llm": {"cloud": {"tool_rounds": 7}}})
        from backend.agent import tool_rounds

        self.assertEqual(tool_rounds(), 7)

    def test_invalid_falls_back(self):
        self._patch_agent_config({"llm": {"cloud": {"tool_rounds": "abc"}}})
        from backend.agent import tool_rounds

        self.assertEqual(tool_rounds(), 500)


class _FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def complete(self, messages, tools=None):
        self.calls.append({"n": len(messages), "tools": tools})
        return self.replies.pop(0)


class _FakeTTS:
    def __init__(self):
        self.spoken = []

    async def speak(self, text):
        self.spoken.append(text)


class _FakeRegistry:
    def __init__(self):
        self.called = []

    def schemas(self):
        return [{"type": "function", "function": {"name": "noop", "parameters": {}}}]

    async def call(self, name, **kwargs):
        self.called.append(name)
        return "ok"

    def get(self, name):
        return None


class TestAgentMultiRound(unittest.TestCase):
    """E3 多轮工具循环：连环工具调用可继续；轮数用尽强制收尾。"""

    def _agent(self, llm):
        import asyncio as _asyncio

        from backend.agent import Agent

        tts, reg = _FakeTTS(), _FakeRegistry()
        agent = Agent(llm=llm, tts=tts, registry=reg)
        return agent, tts, reg

    def test_multi_round_tool_loop(self):
        import asyncio

        tc = {"id": "c1", "type": "function", "function": {"name": "noop", "arguments": "{}"}}
        llm = _FakeLLM([
            Completion(content="", tool_calls=[tc]),
            Completion(content="", tool_calls=[dict(tc, id="c2")]),
            Completion(content="搞定", tool_calls=[]),
        ])
        agent, tts, reg = self._agent(llm)
        asyncio.run(agent.handle("做点事"))
        self.assertEqual(reg.called, ["noop", "noop"])  # 两轮都被执行
        self.assertEqual(len(llm.calls), 3)
        self.assertIsNotNone(llm.calls[1]["tools"])  # 续轮仍带 tools
        self.assertEqual(agent._history[-1].content, "搞定")
        self.assertEqual(tts.spoken[-1], "搞定")

    def test_round_limit_forces_summary(self):
        import asyncio

        import backend.agent as agent_mod

        from backend.config import Config

        old = agent_mod.config
        agent_mod.config = Config({"llm": {"cloud": {"tool_rounds": 1}}})
        self.addCleanup(lambda: setattr(agent_mod, "config", old))

        tc = {"id": "c1", "type": "function", "function": {"name": "noop", "arguments": "{}"}}
        llm = _FakeLLM([
            Completion(content="", tool_calls=[tc]),
            Completion(content="收尾", tool_calls=[]),
        ])
        agent, tts, reg = self._agent(llm)
        asyncio.run(agent.handle("做点事"))
        self.assertEqual(reg.called, ["noop"])
        self.assertEqual(len(llm.calls), 2)
        self.assertIsNone(llm.calls[1]["tools"])  # 强制收尾轮不带 tools
        self.assertEqual(tts.spoken[-1], "收尾")

    def test_pure_chat_untouched(self):
        import asyncio

        llm = _FakeLLM([Completion(content="你好呀", tool_calls=[])])
        agent, tts, reg = self._agent(llm)
        asyncio.run(agent.handle("在吗"))
        self.assertEqual(reg.called, [])
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(tts.spoken, ["你好呀"])


if __name__ == "__main__":
    unittest.main()
