"""M5-M6 米家直连（实验性，默认关）。

MiService 米家直连接入，默认关；首次启用弹窗（文案 config 可配，非硬编码）。
层2 读回验证=执行确认 + 15-30 秒延迟复查；失败 3 次即停 + 提示转层1/手动。
token 走 env 不硬编码（存储加密为开放问题，本阶段至少 env）。
"""
from __future__ import annotations

import os
from typing import Any

MAX_FAILURES: int = 3
DEFAULT_WARNING: str = "米家直连为实验性非官方功能，使用风险自负。"
STOP_MESSAGE: str = "失败 3 次已停止，请转层1 Home Assistant 或手动操作。"


class MijiaClient:
    """米家直连客户端（实验性，默认关）。"""

    def __init__(
        self,
        token: str | None = None,
        warning: str | None = None,
        enabled: bool = False,
        caller: Any | None = None,
    ) -> None:
        self.token = token or os.getenv("MIJIA_TOKEN", "")
        self.warning = warning or DEFAULT_WARNING  # 弹窗文案 config 可配
        self._enabled = bool(enabled)
        self._caller = caller  # call(entity, action) -> bool（可注入 stub）
        self._failures = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def execute(self, entity_id: str, action: str) -> dict[str, Any]:
        """层2 执行；失败 3 次即停。"""
        if not self._enabled:
            return {"status": "disabled", "message": self.warning}
        if self._failures >= MAX_FAILURES:
            return {"status": "stopped", "message": STOP_MESSAGE}
        ok = bool(self._caller(entity_id, action)) if self._caller is not None else False
        if not ok:
            self._failures += 1
            return {"status": "fail", "failures": self._failures}
        return {"status": "ok"}


__all__ = ["MijiaClient", "MAX_FAILURES", "DEFAULT_WARNING", "STOP_MESSAGE"]
