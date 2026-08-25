"""LLM 抽象：统一云端（DeepSeek/通义/豆包等）与本地（Ollama），支持 function calling。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatMessage:
    role: str  # system | user | assistant | tool
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls is not None:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            d["name"] = self.name
        return d


@dataclass
class Completion:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class LLMClient(ABC):
    @abstractmethod
    async def complete(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        """非流式补全，返回文本与可能的工具调用。"""
