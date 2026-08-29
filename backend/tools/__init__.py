"""内置工具注册。

未来接入华为智能家居时，既可在本层新增具体动作工具，
也可在 devices 层实现 HuaweiAdapter 并注册设备动作工具。
"""
from __future__ import annotations

from typing import Callable

from backend.config import config
from backend.tools.base import ToolRegistry, registry
from backend.tools.clipboard import ClipboardTool
from backend.tools.clock import RateTool, TimeTool
from backend.tools.computer import (
    ComputerHotkeyTool,
    ComputerMouseTool,
    ComputerTypeTool,
    ComputerWindowTool,
    ScreenLookTool,
    UiADumpTool,
)
from backend.tools.media import MediaTool
from backend.tools.open_app import OpenAppTool
from backend.tools.remember import RememberTool
from backend.tools.reminder import ReminderTool
from backend.tools.system_control import LockScreenTool, ScreenshotTool, SleepTool, VolumeTool
from backend.tools.weather import WeatherTool
from backend.tools.web_search import WebSearchTool


def register_builtin_tools(on_reminder_fire: Callable[[str], None] | None = None) -> ToolRegistry:
    enabled = config.get(
        "tools.enabled",
        [
            "web_search",
            "open_app",
            "weather",
            "reminder",
            "remember",
            "volume",
            "screenshot",
            "lock_screen",
            "sleep_pc",
            "media",
            "clipboard",
            "time_now",
            "exchange_rate",
            "computer_mouse",
            "computer_type",
            "computer_hotkey",
            "computer_window",
            "screen_look",
            "uia_dump",
        ],
    )
    factories = {
        "web_search": WebSearchTool,
        "open_app": OpenAppTool,
        "weather": WeatherTool,
        "reminder": lambda: ReminderTool(on_fire=on_reminder_fire),
        "remember": RememberTool,
        "volume": VolumeTool,
        "screenshot": ScreenshotTool,
        "lock_screen": LockScreenTool,
        "sleep_pc": SleepTool,
        "media": MediaTool,
        "clipboard": ClipboardTool,
        "time_now": TimeTool,
        "exchange_rate": RateTool,
        "computer_mouse": ComputerMouseTool,
        "computer_type": ComputerTypeTool,
        "computer_hotkey": ComputerHotkeyTool,
        "computer_window": ComputerWindowTool,
        "screen_look": ScreenLookTool,
        "uia_dump": UiADumpTool,
    }
    for name in enabled:
        factory = factories.get(name)
        if factory is not None:
            registry.register(factory())
    return registry
