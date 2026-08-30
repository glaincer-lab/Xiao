"""语音操电脑工具（V3）：鼠标 / 打字 / 热键 / 窗口 / 截屏看图 / UIA 元素树（Windows）。

安全设计（从严，fail-closed）：
- 总开关 `tools.computer.enabled` 默认关，未开启时工具只回一步式引导话术；
- 鼠标点击、打字、热键、关闭窗口默认逐次语音审批（`tools.computer.confirm` 可按类放行）；
- 审批钩子由 core 注入（main.py 里 set_confirm_hook），钩子缺失或审批异常一律拒绝。
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import time
from typing import Any, Awaitable, Callable

from backend.attention import guard_blacklisted_window
from backend.config import ROOT, config
from backend.tools.base import Tool

_confirm_hook: Callable[[str], Awaitable[bool]] | None = None

DEFAULT_CONFIRM = ["mouse", "type", "hotkey", "window_close"]

MOUSEEVENTF_LEFTDOWN = 0x02
MOUSEEVENTF_LEFTUP = 0x04
MOUSEEVENTF_RIGHTDOWN = 0x08
MOUSEEVENTF_RIGHTUP = 0x10
MOUSEEVENTF_WHEEL = 0x0800

KEYEVENTF_KEYUP = 0x02
KEYEVENTF_UNICODE = 0x04

_VK_SPECIAL: dict[str, int] = {
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "space": 0x20,
    "printscreen": 0x2C, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
}


def set_confirm_hook(hook: Callable[[str], Awaitable[bool]] | None) -> None:
    """注入语音审批钩子（core.request_tool_approval），仅 main.py 启动时调用。"""
    global _confirm_hook
    _confirm_hook = hook


def _unsupported() -> str:
    return "这个功能目前只在 Windows 上可用。"


def _confirm_categories() -> list[str]:
    val = config.get("tools.computer.confirm", DEFAULT_CONFIRM)
    return [str(x) for x in val] if isinstance(val, list) else list(DEFAULT_CONFIRM)


def _master_gate() -> str | None:
    if sys.platform != "win32":
        return _unsupported()
    if not bool(config.get("tools.computer.enabled", False)):
        return "语音操电脑还没开启：请在设置 → 执行 → 「语音操电脑」勾选开启后再试。"
    return None


async def _confirm(category: str, question: str) -> str | None:
    if category not in _confirm_categories():
        return None
    hook = _confirm_hook
    if hook is None:
        return "现在语音审批通道不可用，为安全起见我先不执行这一步。"
    try:
        ok = await hook(question)
    except Exception as e:  # noqa: BLE001
        print(f"[computer] 审批钩子异常（按拒绝处理）: {e}")
        return "语音审批出了点问题，为安全起见我先不执行。"
    if not ok:
        return "好，那我不动了。"
    return None


def _ensure_dpi() -> None:
    import ctypes

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def _vk_of(name: str) -> int | None:
    k = str(name).strip().lower()
    if k in _VK_SPECIAL:
        return _VK_SPECIAL[k]
    if len(k) == 1 and k.isalnum():
        return ord(k.upper())
    if len(k) == 2 and k[0] == "f" and k[1:].isdigit():
        idx = int(k[1:])
        if 1 <= idx <= 12:
            return 0x70 + idx - 1
    return None


def _parse_pos(args: dict[str, Any]) -> tuple[int, int] | None:
    try:
        x = int(args.get("x"))
        y = int(args.get("y"))
    except (TypeError, ValueError):
        return None
    return (x, y)


class ComputerMouseTool(Tool):
    name = "computer_mouse"
    description = (
        "控制鼠标：单击/双击/右键/移动/滚动。坐标用屏幕像素，"
        "可先用 screen_look 或 uia_dump 获取元素位置。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["click", "double", "right", "move", "scroll"],
                "description": "click=单击 double=双击 right=右键 move=移动 scroll=滚动",
            },
            "x": {"type": "integer", "description": "目标横坐标（屏幕像素，可省略）"},
            "y": {"type": "integer", "description": "目标纵坐标（屏幕像素，可省略）"},
            "direction": {"type": "string", "enum": ["up", "down"], "description": "滚动方向（scroll 时用）"},
            "amount": {"type": "integer", "description": "滚动格数（scroll 时用，默认 3）"},
        },
        "required": ["action"],
    }

    async def run(self, **kwargs: Any) -> str:
        gate = _master_gate()
        if gate:
            return gate
        blocked = guard_blacklisted_window()
        if blocked:
            return blocked
        action = str(kwargs.get("action") or "click").lower()
        pos = _parse_pos(kwargs)
        if action in ("click", "double", "right", "move") and pos is None:
            return "请告诉我坐标（x、y），可先用 screen_look 或 uia_dump 查看屏幕元素位置。"
        where = f"({pos[0]},{pos[1]})" if pos else "当前位置"
        plan = {
            "click": f"在 {where} 单击鼠标",
            "double": f"在 {where} 双击鼠标",
            "right": f"在 {where} 点右键",
            "move": f"把鼠标移到 {where}",
            "scroll": f"鼠标{'向上' if str(kwargs.get('direction', 'down')) == 'up' else '向下'}滚动",
        }.get(action)
        if plan is None:
            return "不支持的鼠标动作，可用：click / double / right / move / scroll。"
        deny = await _confirm("mouse", f"是否允许：{plan}？请说允许，或者拒绝。")
        if deny:
            return deny
        try:
            await asyncio.to_thread(self._execute, action, pos, kwargs)
        except Exception as e:  # noqa: BLE001
            return f"鼠标操作失败：{e}"
        return f"{plan}，已完成。"

    def _execute(self, action: str, pos: tuple[int, int] | None, kwargs: dict[str, Any]) -> None:
        import ctypes

        _ensure_dpi()
        user32 = ctypes.windll.user32
        if pos is not None:
            user32.SetCursorPos(int(pos[0]), int(pos[1]))
        if action == "click":
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        elif action == "double":
            for _ in range(2):
                user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        elif action == "right":
            user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
            user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
        elif action == "scroll":
            amount = int(kwargs.get("amount") or 3)
            direction = str(kwargs.get("direction") or "down").lower()
            wheel = amount * 120 * (1 if direction == "up" else -1)
            user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, wheel, 0)


class ComputerTypeTool(Tool):
    name = "computer_type"
    description = "向当前活动窗口输入一段文字（模拟键盘逐字输入），可选随后按回车。"
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要输入的文字"},
            "press_enter": {"type": "boolean", "description": "输入完后是否按回车（默认否）"},
        },
        "required": ["text"],
    }

    async def run(self, **kwargs: Any) -> str:
        gate = _master_gate()
        if gate:
            return gate
        text = str(kwargs.get("text") or "")
        if not text:
            return "要输入的文字不能为空。"
        press_enter = bool(kwargs.get("press_enter"))
        plan = f"在当前窗口输入文字「{text[:20]}{'…' if len(text) > 20 else ''}」"
        deny = await _confirm("type", f"是否允许：{plan}？请说允许，或者拒绝。")
        if deny:
            return deny
        try:
            await asyncio.to_thread(self._execute, text, press_enter)
        except Exception as e:  # noqa: BLE001
            return f"输入失败：{e}"
        return f"{plan}，已完成。"

    def _execute(self, text: str, press_enter: bool) -> None:
        import ctypes
        from ctypes import wintypes

        _ensure_dpi()
        user32 = ctypes.windll.user32

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
            ]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [
                ("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD),
            ]

        class INPUTUNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]

        def send_char(ch: str) -> None:
            code = ord(ch)
            for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                inp = INPUT(type=1, union=INPUTUNION(ki=KEYBDINPUT(0, code, flags, 0, None)))
                user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

        for ch in text:
            send_char(ch)
        if press_enter:
            user32.keybd_event(0x0D, 0, 0, 0)
            user32.keybd_event(0x0D, 0, KEYEVENTF_KEYUP, 0)


class ComputerHotkeyTool(Tool):
    name = "computer_hotkey"
    description = "按一组组合键（如 ctrl+s、alt+f4、win+d）。keys 为键名数组，从最后一个普通键开始组合。"
    parameters = {
        "type": "object",
        "properties": {
            "keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "键名数组，如 ['ctrl','s']、['alt','f4']、['win','d']",
            },
        },
        "required": ["keys"],
    }

    async def run(self, **kwargs: Any) -> str:
        gate = _master_gate()
        if gate:
            return gate
        raw = kwargs.get("keys")
        names = [str(k).strip().lower() for k in raw] if isinstance(raw, list) else []
        vks = [(_n, _vk_of(_n)) for _n in names]
        missing = [_n for _n, vk in vks if vk is None]
        if not names or missing:
            return f"有不认识的键名：{('、'.join(missing)) if missing else '（为空）'}，例：ctrl / alt / shift / win / a / f4 / enter。"
        combo = "+".join(names)
        deny = await _confirm("hotkey", f"是否允许：按下组合键 {combo}？请说允许，或者拒绝。")
        if deny:
            return deny
        try:
            await asyncio.to_thread(self._execute, [vk for _, vk in vks])
        except Exception as e:  # noqa: BLE001
            return f"按键失败：{e}"
        return f"已按下 {combo}。"

    def _execute(self, vks: list[int]) -> None:
        import ctypes

        _ensure_dpi()
        user32 = ctypes.windll.user32
        for vk in vks[:-1]:
            user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vks[-1], 0, 0, 0)
        user32.keybd_event(vks[-1], 0, KEYEVENTF_KEYUP, 0)
        for vk in reversed(vks[:-1]):
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def _list_windows() -> list[tuple[int, str]]:
    import ctypes
    from ctypes import wintypes

    _ensure_dpi()
    user32 = ctypes.windll.user32
    out: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def on_window(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if title:
            out.append((int(hwnd), title))
        return True

    user32.EnumWindows(on_window, 0)
    return out


def _find_window(title: str) -> tuple[int, str] | None:
    windows = _list_windows()
    if not title:
        import ctypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        for h, t in windows:
            if h == hwnd:
                return (h, t)
        return windows[0] if windows else None
    t_low = title.lower()
    for h, t in windows:
        if t_low in t.lower():
            return (h, t)
    return None


class ComputerWindowTool(Tool):
    name = "computer_window"
    description = "管理窗口：列出所有窗口标题，或按标题聚焦/最小化/最大化/关闭某个窗口。"
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "focus", "minimize", "maximize", "close"],
                "description": "list=列出窗口 focus=聚焦 minimize=最小化 maximize=最大化 close=关闭",
            },
            "title": {"type": "string", "description": "窗口标题（包含即可，模糊匹配；省略则用当前活动窗口）"},
        },
        "required": ["action"],
    }

    async def run(self, **kwargs: Any) -> str:
        gate = _master_gate()
        if gate:
            return gate
        action = str(kwargs.get("action") or "list").lower()
        title = str(kwargs.get("title") or "")
        if action == "list":
            windows = await asyncio.to_thread(_list_windows)
            if not windows:
                return "没找到可见窗口。"
            shown = [f"{i + 1}. {t}" for i, (_h, t) in enumerate(windows[:30])]
            more = f"（共 {len(windows)} 个，只列前 30）" if len(windows) > 30 else ""
            return "当前窗口：\n" + "\n".join(shown) + more
        found = await asyncio.to_thread(_find_window, title)
        if found is None:
            return f"没找到标题包含「{title or '当前窗口'}」的窗口，可先用 list 动作列出所有窗口。"
        hwnd, wtitle = found
        if action == "close":
            deny = await _confirm(
                "window_close", f"是否允许：关闭窗口「{wtitle}」？请说允许，或者拒绝。"
            )
            if deny:
                return deny
        plan = {"focus": f"把窗口「{wtitle}」调到前台", "minimize": f"最小化窗口「{wtitle}」",
                "maximize": f"最大化窗口「{wtitle}」", "close": f"关闭窗口「{wtitle}」"}.get(action)
        if plan is None:
            return "不支持的窗口动作，可用：list / focus / minimize / maximize / close。"
        try:
            await asyncio.to_thread(self._execute, int(hwnd), action)
        except Exception as e:  # noqa: BLE001
            return f"窗口操作失败：{e}"
        return f"{plan}，已完成。"

    def _execute(self, hwnd: int, action: str) -> None:
        import ctypes

        user32 = ctypes.windll.user32
        if action == "focus":
            user32.SetForegroundWindow(hwnd)
        elif action == "minimize":
            user32.ShowWindow(hwnd, 6)
        elif action == "maximize":
            user32.ShowWindow(hwnd, 3)
        elif action == "close":
            user32.PostMessageW(hwnd, 0x0010, 0, 0)


class ScreenLookTool(Tool):
    name = "screen_look"
    description = (
        "截取当前整个屏幕并保存；若当前模型支持图片输入，会把截图附给模型用来看屏幕内容"
        "（配合 computer_mouse 点坐标，或让小二描述屏幕）。"
    )
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self) -> None:
        self.pending_images: list[str] | None = None

    async def run(self, **kwargs: Any) -> str:
        gate = _master_gate()
        if gate:
            return gate
        blocked = guard_blacklisted_window()
        if blocked:
            return blocked
        try:
            rel, data_url = await asyncio.to_thread(self._capture)
        except Exception as e:  # noqa: BLE001
            return f"截屏失败：{e}"
        vision_on = bool(config.get("llm.cloud.image_input", False))
        self.pending_images = [data_url] if vision_on else None
        if vision_on:
            return f"已截屏并保存到 {rel}，截图已附在本条消息里，请结合截图回答。"
        return f"已截屏并保存到 {rel}。当前未开启图片输入，我暂时看不了图；可在设置 → 大模型 → 「支持图片输入」开启（需视觉模型）。"

    def _capture(self) -> tuple[str, str]:
        from PIL import ImageGrab

        _ensure_dpi()
        out_dir = os.path.join(ROOT, "screenshots")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"screen_{time.strftime('%Y%m%d_%H%M%S')}.jpg")
        try:
            img = ImageGrab.grab(all_screens=True)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"截屏失败（{e}）；请确认已安装图像支持库：pip install Pillow") from e
        img.save(path, "JPEG", quality=80)
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        return rel, f"data:image/jpeg;base64,{b64}"


class UiADumpTool(Tool):
    # 未命名时仍值得展示的控件类型（中英都收，UWP 挂起/英文系统会返回英文）
    _INTERESTING = (
        "按钮", "菜单项", "菜单", "链接", "选项卡", "列表项", "复选框", "组合框",
        "button", "menu item", "menu", "link", "tab item", "list item",
        "check box", "checkbox", "combo box", "edit", "document", "window", "pane",
    )
    name = "uia_dump"
    description = (
        "读取指定窗口（默认前台窗口）的控件元素树：名称、类型、位置，"
        "供后续 computer_mouse 按坐标点击按钮/菜单。需要可选依赖 comtypes。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "窗口标题（包含即可；省略则用当前前台窗口）"},
            "depth": {"type": "integer", "description": "遍历深度（默认 6，最大 12）"},
        },
        "required": [],
    }

    async def run(self, **kwargs: Any) -> str:
        gate = _master_gate()
        if gate:
            return gate
        try:
            import comtypes.client  # noqa: F401
        except ImportError:
            return "读取窗口元素需要先安装 UIA 支持库：请在本机执行 pip install comtypes，装完重启小二再试。"
        title = str(kwargs.get("title") or "")
        try:
            depth = min(max(int(kwargs.get("depth") or 6), 1), 12)
        except (TypeError, ValueError):
            depth = 6
        found = await asyncio.to_thread(_find_window, title)
        if found is None:
            return f"没找到标题包含「{title or '当前窗口'}」的窗口，可先用 computer_window 的 list 动作列出窗口。"
        hwnd, wtitle = found
        try:
            lines = await asyncio.to_thread(self._dump, hwnd, depth)
        except Exception as e:  # noqa: BLE001
            return f"读取窗口元素失败：{e}"
        if not lines:
            return f"窗口「{wtitle}」没有读到可交互元素。"
        head = f"窗口「{wtitle}」的元素（缩进表示层级）：\n"
        return head + "\n".join(lines[:150])

    def _dump(self, hwnd: int, depth: int) -> list[str]:
        import comtypes
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation

        comtypes.CoInitialize()
        try:
            uia = comtypes.client.CreateObject(
                CUIAutomation(), interface=IUIAutomation, clsctx=comtypes.CLSCTX_INPROC_SERVER
            )
            root = uia.ElementFromHandle(int(hwnd))
            lines: list[str] = []

            def walk(el: Any, level: int) -> None:
                if level > depth or len(lines) >= 150:
                    return
                try:
                    name = el.CurrentName or ""
                    ctype = el.CurrentLocalizedControlType or "元素"
                    rect = el.CurrentBoundingRectangle
                    x, y = int(rect.left), int(rect.top)
                    w, h = int(rect.right) - x, int(rect.bottom) - y
                except Exception:  # noqa: BLE001
                    if level == 0:
                        lines.append("- [元素] （该窗口暂时读不出元素：可能已最小化、挂起或受系统保护，可先切到该窗口再试）")
                    return
                if name or level == 0 or ctype.lower() in self._INTERESTING:
                    lines.append(f"{'  ' * level}- [{ctype}] {name} ({x},{y} {w}x{h})")
                try:
                    child = uia.ControlViewWalker.GetFirstChildElement(el)
                except Exception:  # noqa: BLE001
                    return
                while child is not None:
                    walk(child, level + 1)
                    if len(lines) >= 150:
                        return
                    try:
                        child = uia.ControlViewWalker.GetNextSiblingElement(child)
                    except Exception:  # noqa: BLE001
                        break

            walk(root, 0)
            return lines
        finally:
            comtypes.CoUninitialize()
