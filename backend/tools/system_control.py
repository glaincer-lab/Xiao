"""系统控制工具：音量、截图、锁屏、睡眠（Windows 内置能力，零依赖、免 Key）。"""
from __future__ import annotations

import asyncio
import os
import sys

from backend.attention import guard_blacklisted_window
from backend.config import ROOT
from backend.tools.base import Tool

VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF


def _unsupported() -> str:
    return "这个功能目前只在 Windows 上可用。"


def _press_key(vk: int, times: int = 1) -> None:
    import ctypes

    for _ in range(times):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


class VolumeTool(Tool):
    name = "volume"
    description = "调节系统音量：调大、调小、静音/取消静音切换。"
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["up", "down", "mute"],
                "description": "up=调大音量 down=调小音量 mute=静音切换",
            },
        },
        "required": ["action"],
    }

    async def run(self, action: str = "up") -> str:
        if sys.platform != "win32":
            return _unsupported()
        action = str(action or "up").lower()
        await asyncio.to_thread(self._press, action)
        return {
            "up": "已调大音量。",
            "down": "已调小音量。",
            "mute": "已切换静音。",
        }.get(action, "已切换静音。")

    def _press(self, action: str) -> None:
        if action == "up":
            _press_key(VK_VOLUME_UP, 5)
        elif action == "down":
            _press_key(VK_VOLUME_DOWN, 5)
        else:
            _press_key(VK_VOLUME_MUTE, 1)


class ScreenshotTool(Tool):
    name = "screenshot"
    description = "截取整个屏幕，保存到项目的 screenshots 文件夹。"
    parameters = {"type": "object", "properties": {}, "required": []}

    async def run(self) -> str:
        if sys.platform != "win32":
            return _unsupported()
        blocked = guard_blacklisted_window()
        if blocked:
            return blocked
        return await asyncio.to_thread(self._capture)

    def _capture(self) -> str:
        import subprocess
        import time as _time

        out_dir = os.path.join(ROOT, "screenshots")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"shot_{_time.strftime('%Y%m%d_%H%M%S')}.png")
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$vs=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
            "$bmp=New-Object System.Drawing.Bitmap $vs.Width,$vs.Height;"
            "$g=[System.Drawing.Graphics]::FromImage($bmp);"
            "$g.CopyFromScreen($vs.X,$vs.Y,0,0,$bmp.Size);"
            f"$bmp.Save('{path}')"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True,
                timeout=30,
            )
        except Exception as e:  # noqa: BLE001
            return f"截图失败：{e}"
        if os.path.exists(path):
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            return f"已截图，保存在 {rel}。"
        return "截图失败：没有生成图片文件，请稍后再试。"


class LockScreenTool(Tool):
    name = "lock_screen"
    description = "锁定电脑，回到登录界面。"
    parameters = {"type": "object", "properties": {}, "required": []}

    async def run(self) -> str:
        if sys.platform != "win32":
            return _unsupported()
        await asyncio.to_thread(self._lock)
        return "已锁屏。"

    def _lock(self) -> None:
        import ctypes

        ctypes.windll.user32.LockWorkStation()


class SleepTool(Tool):
    name = "sleep_pc"
    description = "让电脑进入睡眠状态。"
    parameters = {"type": "object", "properties": {}, "required": []}

    async def run(self) -> str:
        if sys.platform != "win32":
            return _unsupported()
        await asyncio.to_thread(self._sleep)
        return "正在进入睡眠。"

    def _sleep(self) -> None:
        import subprocess

        subprocess.Popen(
            ["rundll32.exe", "powrprof.dll,SetSuspendState 0,1,0"],
            capture_output=True,
        )
