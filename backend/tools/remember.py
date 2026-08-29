"""remember 工具：把用户明确要求记住的事写进长期记忆（logs/memory.json）。"""
from __future__ import annotations

from typing import Any

from backend.memory import MemoryStore, memory_store
from backend.tools.base import Tool


class RememberTool(Tool):
    name = "remember"
    description = (
        "把用户明确要求记住的事写入长期记忆（跨会话保存，下次对话仍可用）。"
        "仅当用户明确说「记住…」「记一下…」时才调用；普通聊天、随口一提不要调用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要记住的内容，一句话概括，如：用户偏好简短回答",
            }
        },
        "required": ["text"],
    }

    def __init__(self, store: MemoryStore | None = None) -> None:
        self._store = store if store is not None else memory_store

    async def run(self, **kwargs: Any) -> str:
        text = str(kwargs.get("text") or "").strip()
        if not text:
            return "没说记什么，请告诉我要记住的内容。"
        entry = self._store.add(text)
        count = len(self._store.entries())
        return f"已记住：{entry['text']}（长期记忆现有 {count} 条）"
