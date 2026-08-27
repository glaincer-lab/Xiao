"""路由层：决定一句话走「聊天」还是「干活(DSH)」。

- mode=auto：关键词命中走 dsh，否则走 chat
- mode=chat / mode=dsh：手动强制
- 每次决策写路由日志（JSONL），便于事后分析误判、补充规则
"""
from __future__ import annotations

import json
import os
import time

from backend.config import ROOT, config


class Router:
    def __init__(self) -> None:
        self._mode = str(config.get("router.mode", "auto")).lower()
        self._keywords = [str(k) for k in (config.get("router.dsh_keywords", []) or []) if k]
        self._log_path = os.path.join(ROOT, str(config.get("router.log_path", "logs/routes.jsonl")))

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        mode = str(mode).lower()
        if mode in ("auto", "chat", "dsh"):
            self._mode = mode

    def reload_keywords(self) -> None:
        """保存配置后重载关键词（软配置热加载）。"""
        self._keywords = [str(k) for k in (config.get("router.dsh_keywords", []) or []) if k]

    def route(self, text: str) -> str:
        if self._mode == "chat":
            decision = "chat"
        elif self._mode == "dsh":
            decision = "dsh"
        else:
            decision = "dsh" if self._hit(text) else "chat"
        self._log(text, decision)
        return decision

    def _hit(self, text: str) -> bool:
        t = text.lower()
        return any(k.lower() in t for k in self._keywords)

    def _log(self, text: str, decision: str) -> None:
        try:
            os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(
                    {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "mode": self._mode,
                     "decision": decision,
                     "text": text},
                    ensure_ascii=False,
                ) + "\n")
        except Exception as e:  # noqa: BLE001
            print(f"[router] 写路由日志失败: {e}")
