"""语音操电脑单测：总开关 / 逐类审批 fail-closed / 键值解析 / 截图挂接 / 注册 / agent 挂接。

不真实点击鼠标键盘：所有底层 _execute / _capture / _list_windows 均 mock。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from unittest import mock

from backend.config import Config
from backend.llm.base import Completion

import backend.tools.computer as computer_mod
from backend.tools.computer import (
    ComputerMouseTool,
    ComputerHotkeyTool,
    ComputerTypeTool,
    ComputerWindowTool,
    ScreenLookTool,
    UiADumpTool,
    set_confirm_hook,
    reset_approval_cache,
)

_IMG = "data:image/jpeg;base64,QQ=="


class _ComputerCase(unittest.TestCase):
    def setUp(self) -> None:
        set_confirm_hook(None)
        reset_approval_cache()

    def _cfg(self, cfg: dict) -> None:
        old = computer_mod.config
        computer_mod.config = Config(cfg)
        self.addCleanup(setattr, computer_mod, "config", old)

    @staticmethod
    def _hook(ok: bool, log: list[str] | None = None):
        async def hook(question: str) -> bool:
            if log is not None:
                log.append(question)
            return ok

        return hook


class TestMasterGate(_ComputerCase):
    """总开关默认关：所有工具只回一步式引导话术。"""

    def test_default_closed_guides(self):
        self._cfg({"tools": {"computer": {"enabled": False}}})
        out = asyncio.run(ComputerMouseTool().run(action="click", x=1, y=1))
        self.assertIn("语音操电脑还没开启", out)

    def test_all_tools_gated_when_closed(self):
        self._cfg({"tools": {"computer": {"enabled": False}}})
        outs = [
            asyncio.run(ComputerMouseTool().run(action="scroll")),
            asyncio.run(ComputerTypeTool().run(text="hi")),
            asyncio.run(ComputerHotkeyTool().run(keys=["ctrl", "s"])),
            asyncio.run(ComputerWindowTool().run(action="list")),
            asyncio.run(ScreenLookTool().run()),
            asyncio.run(UiADumpTool().run()),
        ]
        for out in outs:
            self.assertIn("语音操电脑还没开启", out)

    def test_non_win32_rejected(self):
        self._cfg({"tools": {"computer": {"enabled": True}}})
        with mock.patch.object(sys, "platform", "linux"):
            out = asyncio.run(ScreenLookTool().run())
        self.assertIn("只在 Windows 上可用", out)


class TestConfirmFlow(_ComputerCase):
    """逐类审批：钩子缺失/异常一律拒绝（fail-closed），拒绝即停，允许才执行。"""

    def test_missing_hook_rejects(self):
        self._cfg({"tools": {"computer": {"enabled": True}}})
        with mock.patch.object(ComputerMouseTool, "_execute") as ex:
            out = asyncio.run(ComputerMouseTool().run(action="click", x=1, y=2))
        self.assertIn("语音审批通道不可用", out)
        self.assertFalse(ex.called)

    def test_deny_stops_before_execute(self):
        self._cfg({"tools": {"computer": {"enabled": True}}})
        set_confirm_hook(self._hook(False))
        with mock.patch.object(ComputerTypeTool, "_execute") as ex:
            out = asyncio.run(ComputerTypeTool().run(text="hello"))
        self.assertIn("不", out)
        self.assertFalse(ex.called)

    def test_allow_executes(self):
        self._cfg({"tools": {"computer": {"enabled": True}}})
        set_confirm_hook(self._hook(True))
        with mock.patch.object(ComputerMouseTool, "_execute") as ex:
            out = asyncio.run(ComputerMouseTool().run(action="double", x=3, y=4))
        self.assertIn("已完成", out)
        self.assertTrue(ex.called)

    def test_hook_error_fails_closed(self):
        self._cfg({"tools": {"computer": {"enabled": True}}})

        async def boom(_q: str) -> bool:
            raise RuntimeError("hook down")

        set_confirm_hook(boom)
        with mock.patch.object(ComputerMouseTool, "_execute") as ex:
            out = asyncio.run(ComputerMouseTool().run(action="click", x=1, y=2))
        self.assertIn("出了点问题", out)
        self.assertFalse(ex.called)

    def test_category_opt_out_skips_confirm(self):
        self._cfg({"tools": {"computer": {"enabled": True, "confirm": []}}})
        with mock.patch.object(ComputerHotkeyTool, "_execute") as ex:
            out = asyncio.run(ComputerHotkeyTool().run(keys=["ctrl", "s"]))
        self.assertIn("已按下", out)
        self.assertTrue(ex.called)

    def test_question_mentions_action(self):
        self._cfg({"tools": {"computer": {"enabled": True}}})
        log: list[str] = []
        set_confirm_hook(self._hook(True, log))
        with mock.patch.object(ComputerMouseTool, "_execute"):
            asyncio.run(ComputerMouseTool().run(action="click", x=5, y=6))
        self.assertEqual(len(log), 1)
        self.assertIn("单击鼠标", log[0])

    def test_mouse_without_coords_guides(self):
        self._cfg({"tools": {"computer": {"enabled": True, "confirm": []}}})
        out = asyncio.run(ComputerMouseTool().run(action="click"))
        self.assertIn("坐标", out)

    def test_unknown_action_rejected(self):
        self._cfg({"tools": {"computer": {"enabled": True, "confirm": []}}})
        out = asyncio.run(ComputerMouseTool().run(action="drag", x=1, y=1))
        self.assertIn("不支持", out)


class TestParsing(_ComputerCase):
    def test_vk_special_and_alnum_and_fkeys(self):
        self.assertEqual(computer_mod._vk_of("ctrl"), 0x11)
        self.assertEqual(computer_mod._vk_of("a"), ord("A"))
        self.assertEqual(computer_mod._vk_of("f4"), 0x70 + 3)
        self.assertIsNone(computer_mod._vk_of("f13"))
        self.assertIsNone(computer_mod._vk_of("foo"))

    def test_parse_pos(self):
        self.assertEqual(computer_mod._parse_pos({"x": 10, "y": 20}), (10, 20))
        self.assertIsNone(computer_mod._parse_pos({"x": "a", "y": 1}))
        self.assertIsNone(computer_mod._parse_pos({}))


class TestWindowTool(_ComputerCase):
    def test_close_requires_confirm(self):
        self._cfg({"tools": {"computer": {"enabled": True}}})
        set_confirm_hook(self._hook(False))
        with mock.patch.object(computer_mod, "_find_window", return_value=(123, "记事本")), \
                mock.patch.object(ComputerWindowTool, "_execute") as ex:
            out = asyncio.run(ComputerWindowTool().run(action="close", title="记事本"))
        self.assertIn("不动", out)
        self.assertFalse(ex.called)

    def test_list_needs_no_hook(self):
        self._cfg({"tools": {"computer": {"enabled": True}}})
        with mock.patch.object(computer_mod, "_list_windows", return_value=[(1, "A"), (2, "B")]):
            out = asyncio.run(ComputerWindowTool().run(action="list"))
        self.assertIn("1. A", out)
        self.assertIn("2. B", out)

    def test_focus_executes(self):
        self._cfg({"tools": {"computer": {"enabled": True}}})
        with mock.patch.object(computer_mod, "_find_window", return_value=(9, "A")), \
                mock.patch.object(ComputerWindowTool, "_execute") as ex:
            out = asyncio.run(ComputerWindowTool().run(action="focus", title="A"))
        self.assertIn("已完成", out)
        self.assertTrue(ex.called)

    def test_window_not_found(self):
        self._cfg({"tools": {"computer": {"enabled": True}}})
        with mock.patch.object(computer_mod, "_find_window", return_value=None):
            out = asyncio.run(ComputerWindowTool().run(action="focus", title="不存在"))
        self.assertIn("没找到", out)


class TestScreenLook(_ComputerCase):
    def test_vision_on_attaches_pending(self):
        self._cfg({"tools": {"computer": {"enabled": True}}, "llm": {"cloud": {"image_input": True}}})
        tool = ScreenLookTool()
        with mock.patch.object(ScreenLookTool, "_capture", return_value=("screenshots/s.jpg", _IMG)):
            out = asyncio.run(tool.run())
        self.assertIn("截图已附", out)
        self.assertEqual(tool.pending_images, [_IMG])

    def test_vision_off_no_pending(self):
        self._cfg({"tools": {"computer": {"enabled": True}, "llm": {"cloud": {"image_input": False}}}})
        tool = ScreenLookTool()
        with mock.patch.object(ScreenLookTool, "_capture", return_value=("screenshots/s.jpg", _IMG)):
            out = asyncio.run(tool.run())
        self.assertIn("未开启图片输入", out)
        self.assertIsNone(tool.pending_images)


class TestUiADump(_ComputerCase):
    def test_missing_comtypes_guides(self):
        self._cfg({"tools": {"computer": {"enabled": True}}})
        with mock.patch.dict(sys.modules, {"comtypes.client": None}):
            out = asyncio.run(UiADumpTool().run())
        self.assertIn("pip install comtypes", out)

    def test_dump_walks_and_reports(self):
        self._cfg({"tools": {"computer": {"enabled": True}}})
        with mock.patch.dict(sys.modules, {"comtypes": mock.MagicMock(), "comtypes.client": mock.MagicMock()}), \
                mock.patch.object(computer_mod, "_find_window", return_value=(1, "A")), \
                mock.patch.object(UiADumpTool, "_dump", return_value=["- [按钮] 确定 (0,0 1x1)"]):
            out = asyncio.run(UiADumpTool().run(title="A", depth=99))
        self.assertIn("[按钮]", out)
        self.assertIn("确定", out)


class _FakeTool:
    name = "screen_look"

    def __init__(self) -> None:
        self.pending_images: list[str] | None = None


class _SeqLLM:
    def __init__(self) -> None:
        self.calls: list[list] = []
        self.replies = [
            Completion(
                content="好的",
                tool_calls=[{"id": "t1", "type": "function", "function": {"name": "screen_look", "arguments": "{}"}}],
            ),
            Completion(content="看到了", tool_calls=[]),
        ]

    async def complete(self, messages, tools=None):
        self.calls.append(list(messages))
        return self.replies[min(len(self.calls), len(self.replies)) - 1]


class _RegWithGet:
    def __init__(self, tool: _FakeTool) -> None:
        self.tool = tool

    def schemas(self):
        return []

    async def call(self, name, **kwargs):
        return "已截屏并保存"

    def get(self, name):
        return self.tool if name == "screen_look" else None


class TestAgentPendingImages(unittest.TestCase):
    """工具截图 → agent 循环把 pending_images 追加为带图 user 消息并清空。"""

    def test_pending_images_become_user_message(self):
        from backend.agent import Agent

        class _TTS:
            async def speak(self, text):
                pass

        tool = _FakeTool()
        tool.pending_images = [_IMG]
        llm = _SeqLLM()
        agent = Agent(llm=llm, tts=_TTS(), registry=_RegWithGet(tool))
        asyncio.run(agent.handle("看看屏幕"))
        visual = [m for m in llm.calls[1] if getattr(m, "role", "") == "user" and m.images]
        self.assertEqual(len(visual), 1)
        self.assertEqual(visual[0].images, [_IMG])
        self.assertIsNone(tool.pending_images)


class TestRequestToolApproval(unittest.TestCase):
    def _p(self):
        from backend.core import Pipeline

        return Pipeline.__new__(Pipeline)

    def test_allowed_once_true(self):
        p = self._p()

        async def fake(action, *, prompt=None, on_timeout="rejected"):
            return "allowed-once"

        p.request_approval = fake
        self.assertTrue(asyncio.run(p.request_tool_approval("点一下")))

    def test_rejected_or_unavailable_false(self):
        p = self._p()

        async def fake(action, *, prompt=None, on_timeout="rejected"):
            return "rejected"

        p.request_approval = fake
        self.assertFalse(asyncio.run(p.request_tool_approval("点一下")))

        async def fake2(action, *, prompt=None, on_timeout="rejected"):
            return "unavailable"

        p.request_approval = fake2
        self.assertFalse(asyncio.run(p.request_tool_approval("点一下")))


class TestRegistration(unittest.TestCase):
    def test_builtin_registers_six_computer_tools(self):
        import backend.tools as tools_pkg
        from backend.tools.base import registry

        old = tools_pkg.config
        tools_pkg.config = Config(
            {"tools": {"enabled": ["computer_mouse", "computer_type", "computer_hotkey", "computer_window", "screen_look", "uia_dump"]}}
        )
        self.addCleanup(setattr, tools_pkg, "config", old)
        tools_pkg.register_builtin_tools()
        names = ("computer_mouse", "computer_type", "computer_hotkey", "computer_window", "screen_look", "uia_dump")
        try:
            for n in names:
                self.assertIsNotNone(registry.get(n))
        finally:
            for n in names:
                registry.unregister(n)


class TestPermsComputer(unittest.TestCase):
    def test_category_registered(self):
        from backend.perms import CATEGORIES

        self.assertIn(("computer", "操电脑", "语音控制鼠标键盘与 GUI 操作"), CATEGORIES)

    def test_predict_hits_computer(self):
        import backend.perms as perms_mod

        old = perms_mod.config
        perms_mod.config = Config({"perms": {"standing_grants": [], "rules": {"computer": ["帮我点", "点击"]}}})
        self.addCleanup(setattr, perms_mod, "config", old)
        perms = perms_mod.Perms()
        self.assertIn("computer", perms.predict("帮我点击保存按钮"))


if __name__ == "__main__":
    unittest.main()
