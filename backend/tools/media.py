"""媒体播放控制工具：通过 Windows 媒体键控制音乐/视频播放，零依赖、免 Key。"""
from __future__ import annotations

import asyncio
import sys

from backend.tools.base import Tool

VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_NEXT_TRACK = 0xB0

_REPLIES = {
    "play_pause": "已切换播放。",
    "next": "下一首。",
    "prev": "上一首。",
    "stop": "已停止播放。",
}


def _press_key(vk: int) -> None:
    import ctypes

    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


class MediaTool(Tool):
    name = "media"
    description = "控制音乐/视频播放：播放或暂停、下一首、上一首、停止。"
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["play_pause", "next", "prev", "stop"],
                "description": "play_pause=播放/暂停切换 next=下一首 prev=上一首 stop=停止",
            },
        },
        "required": ["action"],
    }

    async def run(self, action: str = "play_pause") -> str:
        if sys.platform != "win32":
            return "这个功能目前只在 Windows 上可用。"
        action = str(action or "play_pause").lower()
        vk = {
            "play_pause": VK_MEDIA_PLAY_PAUSE,
            "next": VK_MEDIA_NEXT_TRACK,
            "prev": VK_MEDIA_PREV_TRACK,
            "stop": VK_MEDIA_STOP,
        }.get(action)
        if vk is None:
            return "没听懂要怎么控制播放，可以说播放、暂停、下一首。"
        await asyncio.to_thread(_press_key, vk)
        return _REPLIES[action]
