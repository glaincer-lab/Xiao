"""提醒工具：倒计时后语音提醒。"""
from __future__ import annotations

import asyncio
from typing import Callable

from backend.tools.base import Tool


class ReminderTool(Tool):
    name = "reminder"
    description = "设置倒计时提醒，到时间后语音提醒。"
    parameters = {
        "type": "object",
        "properties": {
            "seconds": {"type": "number", "description": "多少秒后提醒"},
            "message": {"type": "string", "description": "提醒内容"},
        },
        "required": ["seconds", "message"],
    }

    def __init__(self, on_fire: Callable[[str], None] | None = None) -> None:
        self._on_fire = on_fire

    async def run(self, seconds: float, message: str) -> str:
        delay = max(1, int(seconds))
        asyncio.create_task(self._fire(delay, message))
        return f"好的，{delay} 秒后提醒你：{message}"

    async def _fire(self, delay: int, message: str) -> None:
        await asyncio.sleep(delay)
        if self._on_fire is not None:
            await self._on_fire(f"提醒：{message}")
