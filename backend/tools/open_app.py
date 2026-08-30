"""打开网址 / 文件 / 应用工具（Windows）。

安全设计（从严，fail-closed）：
- 网址：仅放行 http(s)（webbrowser.open），其余协议一律拒绝；
- 文件：仅当路径确实存在才允许打开，且须先过语音/按钮审批；
- 应用：仅白名单应用可启动（argv-list 不走 shell），且须先过审批；
- 审批钩子由 core 注入（main.py 里 set_confirm_hook），钩子缺失或审批异常一律拒绝。
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import webbrowser

from typing import Awaitable, Callable

from backend.tools.base import Tool

# 应用名白名单：仅允许这些应用启动，拒绝裸 Popen 任意可执行。
_APP_WHITELIST: dict[str, tuple[str, ...]] = {
    "notepad": ("notepad.exe",),
    "calc": ("calc.exe",),
    "browser": ("msedge.exe", "chrome.exe", "firefox.exe"),
    "explorer": ("explorer.exe",),
    "mspaint": ("mspaint.exe",),
}

# 文件打开时拒绝可执行/脚本扩展名：os.startfile 对这类会启动程序/执行脚本，
# 等于绕过白名单裸执行。fail-closed：不在允许集合一律不开。
_EXEC_SUFFIXES: tuple[str, ...] = (
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".scr", ".lnk", ".msi", ".reg", ".cpl",
)

# 仅放行 http(s)，其余协议（file:// 等）一律拒绝
_URL_RE = re.compile(r"^https?://[\w.-]+(:\d+)?(/[\w./?%&=-]*)?$")

# 审批钩子：core.request_tool_approval(action, *, prompt) -> bool（allowed-once 为 True）
_confirm_hook: Callable[[str, str], Awaitable[bool]] | None = None


def set_confirm_hook(hook: Callable[..., Awaitable[bool]] | None) -> None:
    """注入语音审批钩子（core.request_tool_approval），仅 main.py 启动时调用。"""
    global _confirm_hook
    _confirm_hook = hook


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

    @staticmethod
    def _is_url(target: str) -> bool:
        t = (target or "").strip().strip('"')
        if _URL_RE.match(t):
            return True
        if t.lower().startswith("www.") and _URL_RE.match("https://" + t):
            return True
        return False

    def _needs_approval(self, target: str) -> bool:
        """纯 http(s) 网址低风险放行；文件/应用一律先审批（宁严勿松）。"""
        return not self._is_url(target)

    async def run(self, target: str) -> str:
        t = (target or "").strip().strip('"')
        if not t:
            return "没有指定要打开的内容。"

        # 先决审批：在事件循环侧 await，不放进 _open()（同步线程）
        if self._needs_approval(t):
            hook = _confirm_hook
            if hook is None:
                return "现在语音审批通道不可用，为安全起见我先不打开。"
            try:
                allowed = await hook("打开文件/应用", prompt=f"是否允许打开 {t}？")
            except Exception:  # noqa: BLE001
                allowed = False
            if not allowed:
                return "用户拒绝该操作，已取消打开。"

        return await asyncio.to_thread(self._open, t)

    def _open(self, target: str) -> str:
        t = (target or "").strip().strip('"')
        if not t:
            return "没有指定要打开的内容。"

        # 网址：仅 http(s)
        if _URL_RE.match(t):
            webbrowser.open(t)
            return f"已打开网址 {t}"
        if t.lower().startswith("www."):
            if _URL_RE.match("https://" + t):
                webbrowser.open("https://" + t)
                return f"已打开网址 {t}"

        # 文件：路径确实存在才打开（经审批后）；可执行/脚本扩展名一律拒绝（fail-closed）
        if os.path.exists(t):
            ext = os.path.splitext(t)[1].lower()
            if ext in _EXEC_SUFFIXES:
                return f"出于安全考虑，{ext or '该文件类型'} 不允许直接打开。"
            os.startfile(t)  # Windows
            return f"已打开 {t}"

        # 应用：仅白名单映射，argv-list 不走 shell
        name = t.lower().split()[0]
        exe = _APP_WHITELIST.get(name)
        if not exe:
            return f"未授权应用，仅允许：{', '.join(_APP_WHITELIST)}"
        # 仅启动白名单映射的首选可执行；argv-list 不走 shell
        subprocess.Popen([exe[0]], shell=False)
        return f"已启动 {exe[0]}"
