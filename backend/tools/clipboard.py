"""剪贴板工具：复制文本、粘贴到前台窗口、朗读剪贴板内容。"""
from __future__ import annotations

import asyncio
import os
import sys

from backend.tools.base import Tool

VK_CONTROL = 0x11
VK_V = 0x56

_READ_MAX = 200


def _set_clipboard(text: str) -> None:
    """经 PowerShell Set-Clipboard 写入，避免引号/编码问题。"""
    import subprocess
    import tempfile

    tmp = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", encoding="utf-8-sig", delete=False
        ) as f:
            f.write(text)
            tmp = f.name
        subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"Get-Content -LiteralPath '{tmp}' -Raw -Encoding UTF8 | Set-Clipboard",
            ],
            capture_output=True,
            timeout=15,
        )
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _get_clipboard() -> str:
    """经 PowerShell Get-Clipboard 读取（强制 UTF-8 输出，避免中文乱码）。"""
    import subprocess

    r = subprocess.run(
        [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-Clipboard | Out-String",
        ],
        capture_output=True,
        timeout=15,
    )
    return r.stdout.decode("utf-8", errors="replace").strip()


def _paste_keystroke() -> None:
    import ctypes

    ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_V, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_V, 0, 2, 0)  # KEYEVENTF_KEYUP
    ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 2, 0)


class ClipboardTool(Tool):
    name = "clipboard"
    description = "剪贴板操作：复制一段文字、把剪贴板粘贴到当前窗口、朗读剪贴板内容。"
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["copy", "paste", "read"],
                "description": "copy=复制 text 到剪贴板 paste=向当前窗口粘贴剪贴板 read=读出剪贴板内容",
            },
            "text": {"type": "string", "description": "copy 时要复制的文字"},
        },
        "required": ["action"],
    }

    async def run(self, action: str = "read", text: str = "") -> str:
        if sys.platform != "win32":
            return "这个功能目前只在 Windows 上可用。"
        action = str(action or "read").lower()
        if action == "copy":
            t = (text or "").strip()
            if not t:
                return "要复制什么内容？可以说：把某某复制到剪贴板。"
            await asyncio.to_thread(_set_clipboard, t)
            return "已复制到剪贴板。"
        if action == "paste":
            await asyncio.to_thread(_paste_keystroke)
            return "已粘贴到当前窗口。"
        content = await asyncio.to_thread(_get_clipboard)
        if not content:
            return "剪贴板是空的。"
        if len(content) > _READ_MAX:
            return f"剪贴板内容：{content[:_READ_MAX]}，后面还有内容就不念了。"
        return f"剪贴板内容：{content}"
