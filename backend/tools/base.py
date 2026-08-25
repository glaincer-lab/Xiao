"""可插拔工具层：Tool 抽象 + 注册表。

每个工具以 OpenAI function calling 的 JSON Schema 描述参数，
由 LLM 决定调用哪个工具。未来接入华为智能家居时，
既可通过本层注册具体动作，也可通过 devices 层的 DeviceAdapter（见 devices/base.py）
抽象设备协议，保持语音核心与具体设备解耦。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    name: str
    description: str
    # OpenAI function calling 格式的 JSON Schema
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    @abstractmethod
    async def run(self, **kwargs: Any) -> str:
        """执行工具，返回给大模型看的文本结果。"""
        raise NotImplementedError


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    async def call(self, name: str, **kwargs: Any) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"未知工具: {name}"
        try:
            return await tool.run(**kwargs)
        except Exception as e:  # noqa: BLE001
            return f"工具 {name} 执行失败: {e}"


registry = ToolRegistry()
