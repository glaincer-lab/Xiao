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

from backend.config import Config
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


if __name__ == "__main__":
    unittest.main()
