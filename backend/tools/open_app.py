"""打开网址 / 文件 / 应用工具（Windows）。"""
from __future__ import annotations

import asyncio

from backend.tools.base import Tool


class OpenAppTool(Tool):
    name = "open_app"
    description = "在电脑上打开网址、文件或启动应用程序。"
    parameters = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "要打开的网址、文件路径或应用名"},
        },
        "required": ["target"],
    }

    async def run(self, target: str) -> str:
        return await asyncio.to_thread(self._open, target)

    def _open(self, target: str) -> str:
        import os
        import subprocess
        import webbrowser

        t = (target or "").strip()
        if not t:
            return "没有指定要打开的内容。"

        if t.lower().startswith(("http://", "https://", "www.")):
            if t.lower().startswith("www."):
                t = "https://" + t
            webbrowser.open(t)
            return f"已打开网址 {t}"

        if os.path.exists(t):
            os.startfile(t)  # Windows
            return f"已打开 {t}"

        try:
            subprocess.Popen([t])
            return f"已启动应用 {t}"
        except Exception:
            try:
                os.startfile(t)
                return f"已打开 {t}"
            except Exception as e:  # noqa: BLE001
                return f"无法打开 {t}：{e}"
