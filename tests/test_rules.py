"""B1 L0 规则指令测试：触发词匹配 / 参数提取 / 词表覆盖与开关 / 工具注册。

运行（项目根目录）：
    python -m unittest tests.test_rules -v
"""
from __future__ import annotations

import asyncio
import unittest

import backend.rules as rules_mod
import backend.tools as tools_pkg
import backend.tools.weather as weather_mod
from backend.config import Config
from backend.rules import RuleEngine
from backend.tools.base import registry
from backend.tools.clock import TimeTool

_NEW_TOOLS = [
    "volume",
    "screenshot",
    "lock_screen",
    "sleep_pc",
    "media",
    "clipboard",
    "time_now",
    "exchange_rate",
]


class RuleTestBase(unittest.TestCase):
    def setUp(self):
        c = Config({})
        for mod in (rules_mod, weather_mod, tools_pkg):
            old = getattr(mod, "config")
            setattr(mod, "config", c)
            self.addCleanup(setattr, mod, "config", old)


class TestRuleMatch(RuleTestBase):
    def _match(self, text: str):
        return RuleEngine().match(text)

    def test_open_alias(self):
        hit = self._match("打开记事本")
        self.assertEqual(hit["tool"], "open_app")
        self.assertEqual(hit["kwargs"], {"target": "notepad"})

    def test_open_browser_alias(self):
        hit = self._match("帮我打开浏览器")
        self.assertEqual(hit["kwargs"], {"target": "https://www.bing.com"})

    def test_open_url_preserved(self):
        hit = self._match("打开https://www.example.com/page")
        self.assertEqual(hit["kwargs"], {"target": "https://www.example.com/page"})

    def test_open_bare_falls_through(self):
        self.assertIsNone(self._match("打开"))

    def test_volume_up_and_mute(self):
        self.assertEqual(self._match("音量调大")["kwargs"], {"action": "up"})
        self.assertEqual(self._match("小点声")["kwargs"], {"action": "down"})
        self.assertEqual(self._match("静音")["kwargs"], {"action": "mute"})
        self.assertEqual(self._match("取消静音")["kwargs"], {"action": "mute"})

    def test_screenshot(self):
        hit = self._match("帮我截个图")
        self.assertEqual(hit["tool"], "screenshot")
        self.assertEqual(hit["kwargs"], {})

    def test_lock_speaks_first(self):
        hit = self._match("锁屏")
        self.assertTrue(hit["speak_first"])
        self.assertEqual(hit["reply"], "好的，马上锁屏。")

    def test_media(self):
        self.assertEqual(self._match("下一首")["kwargs"], {"action": "next"})
        self.assertEqual(self._match("上一首")["kwargs"], {"action": "prev"})
        self.assertEqual(self._match("停止播放")["kwargs"], {"action": "stop"})
        self.assertEqual(self._match("暂停一下")["kwargs"], {"action": "play_pause"})

    def test_clipboard_copy_extracts_content(self):
        hit = self._match("把会议纪要复制到剪贴板")
        self.assertEqual(hit["kwargs"], {"action": "copy", "text": "会议纪要"})

    def test_clipboard_read_and_paste(self):
        self.assertEqual(self._match("朗读剪贴板")["kwargs"], {"action": "read"})
        self.assertEqual(self._match("粘贴")["kwargs"], {"action": "paste"})

    def test_time_now(self):
        self.assertEqual(self._match("现在几点")["tool"], "time_now")
        self.assertEqual(self._match("今天星期几")["tool"], "time_now")

    def test_weather_city_extracted(self):
        hit = self._match("查一下上海的天气")
        self.assertEqual(hit["kwargs"], {"city": "上海"})

    def test_weather_auto_locate(self):
        hit = self._match("今天天气怎么样")
        self.assertEqual(hit["kwargs"], {"city": ""})

    def test_exchange_rate(self):
        self.assertEqual(self._match("日元兑人民币汇率")["kwargs"], {"base": "JPY"})
        self.assertEqual(self._match("查一下汇率")["kwargs"], {"base": "USD"})

    def test_reminder_chinese_duration(self):
        hit = self._match("三分钟后提醒我喝水")
        self.assertEqual(hit["kwargs"], {"seconds": 180.0, "message": "喝水"})

    def test_remider_mixed_durations(self):
        self.assertEqual(self._match("倒计时十秒")["kwargs"], {"seconds": 10.0, "message": "时间到了"})
        self.assertEqual(self._match("提醒我5分钟开会")["kwargs"], {"seconds": 300.0, "message": "开会"})

    def test_reminder_without_duration_falls_through(self):
        self.assertIsNone(self._match("提醒我明天开会"))
        self.assertIsNone(self._match("定时任务怎么写"))

    def test_plain_chat_falls_through(self):
        self.assertIsNone(self._match("帮我写一个爬虫脚本"))
        self.assertIsNone(self._match("你是谁"))


class TestRuleConfig(RuleTestBase):
    def _engine_with(self, data: dict) -> RuleEngine:
        rules_mod.config = Config(data)
        return RuleEngine()

    def test_disabled_switch(self):
        engine = self._engine_with({"router": {"rules": {"enabled": False}}})
        self.assertIsNone(engine.match("锁屏"))

    def test_keyword_override(self):
        engine = self._engine_with({"router": {"rules": {"keywords": {"weather": ["气象"]}}}})
        self.assertIsNone(engine.match("今天天气怎么样"))
        hit = engine.match("北京气象如何")
        self.assertEqual(hit["kwargs"], {"city": "北京"})


class TestRuleTools(RuleTestBase):
    def test_time_tool_text(self):
        text = asyncio.run(TimeTool().run())
        self.assertIn("今天", text)
        self.assertIn("现在时间", text)

    def test_register_builtin_defaults(self):
        before = {t.name for t in registry.all()}
        tools_pkg.register_builtin_tools()
        added = [n for n in _NEW_TOOLS if registry.get(n) is not None]
        for n in added:
            self.addCleanup(registry.unregister, n)
        self.assertEqual(sorted(added), sorted(_NEW_TOOLS))
        self.assertIsNotNone(before)


if __name__ == "__main__":
    unittest.main()
