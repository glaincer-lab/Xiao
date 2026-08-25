"""内置工具注册。

未来接入华为智能家居时，既可在本层新增具体动作工具，
也可在 devices 层实现 HuaweiAdapter 并注册设备动作工具。
"""
from __future__ import annotations

from typing import Callable

from backend.config import config
from backend.tools.base import ToolRegistry, registry
from backend.tools.open_app import OpenAppTool
from backend.tools.reminder import ReminderTool
from backend.tools.weather import WeatherTool
from backend.tools.web_search import WebSearchTool


def register_builtin_tools(on_reminder_fire: Callable[[str], None] | None = None) -> ToolRegistry:
    enabled = config.get("tools.enabled", ["web_search", "open_app", "weather", "reminder"])
    factories = {
        "web_search": WebSearchTool,
        "open_app": OpenAppTool,
        "weather": WeatherTool,
        "reminder": lambda: ReminderTool(on_fire=on_reminder_fire),
    }
    for name in enabled:
        factory = factories.get(name)
        if factory is not None:
            registry.register(factory())
    return registry
