"""会话状态与线程安全事件总线。

所有组件（音频管线 / 代理循环 / 工具）通过 EventBus 上抛事件，
WebSocket 层订阅后转发给前端，实现流式上屏与状态切换。
"""
from __future__ import annotations

import enum
import threading
from typing import Any, Callable


class State(str, enum.Enum):
    IDLE = "idle"              # 待机，监听唤醒词
    LISTENING = "listening"    # 听用户说话
    PROCESSING = "processing"  # 理解意图 / 生成计划
    SPEAKING = "speaking"      # 语音播报中
    EXECUTING = "executing"    # 执行工具中
    SLEEPING = "sleeping"      # 会话静音超时，休眠待唤醒
    CONFIRM_SHUTDOWN = "confirm_shutdown"  # 等待用户确认关闭程序
    WORKING = "working"        # DSH 长任务执行中（仍监听进展/取消）
    AWAIT_APPROVAL = "await_approval"  # 等待用户语音确认是否允许执行（A2 审批）


class EventBus:
    """线程安全的发布/订阅总线。"""

    def __init__(self) -> None:
        self._subs: list[Callable[[dict[str, Any]], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(fn)

        def _unsub() -> None:
            with self._lock:
                if fn in self._subs:
                    self._subs.remove(fn)

        return _unsub

    def emit(self, event: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(event)
            except Exception:
                pass  # 单个订阅者异常不影响其它订阅者


bus = EventBus()


def emit(type_: str, **kwargs: Any) -> None:
    bus.emit({"type": type_, **kwargs})
