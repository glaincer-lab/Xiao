"""M3-M3 同类窗口聚合（防事件风暴）（backend/m3/aggregate.py）。

实现 M3-proactive.md §4.3 事件触发的「同类窗口聚合（防事件风暴）」：
    同类事件在时间窗口（可配置，如 30 分钟）内只触发一次；窗口外的新同类事件可再触发。

纯函数/可注入 now_fn（不真实 sleep），并记录聚合日志（供「防风暴」断言）：
    - should_trigger(key, now) -> bool  首次/窗口外 fired；窗口内 same-key 抑制（suppressed）
    - log：每次判定记 {"key","ts","action":"fired"|"suppressed"}
    - last_fired(key) / reset()

边界（写死）：本文件只做「同类防风暴」，不生成候选、不消费、不发布事件；
候选生成与消费由 M3-M3 event_trigger.py + M3-M1 notify.process() 负责。

仅供标准库；MIT。
"""
from __future__ import annotations

import time
from typing import Callable

# 同类窗口默认时长：30 分钟（§4.3 可配置，如 30 分钟）
DEFAULT_WINDOW_SECONDS: float = 1800.0

# 聚合日志动作常量
ACTION_FIRED = "fired"            # 允许触发（首次 / 窗口外）
ACTION_SUPPRESSED = "suppressed"  # 窗口内同类 → 抑制（防风暴）


class EventWindowAggregator:
    """同类事件在时间窗口内只触发一次（防事件风暴）。

    用法：
        agg = EventWindowAggregator(window_seconds=1800, now_fn=time.time)
        if agg.should_trigger("env.anomaly"):   # 首触发 → True
            ...                                  # 窗口内再同 key → False
    纯判定、无副作用；在无窗口需求时（window_seconds<=0）恒放行（防风暴关闭）。
    """

    def __init__(
        self,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._window: float = float(window_seconds)
        self._now_fn: Callable[[], float] = now_fn or time.time
        # key -> 最近一次「有效触发」时间戳（epoch 秒）
        self._last_fired: dict[str, float] = {}
        # 聚合日志（防风暴断言/诊断）
        self.log: list[dict] = []

    def should_trigger(self, key: str, now: float | None = None) -> bool:
        """给定同类判定 key，返回此刻是否允许触发。

        - now 缺省时取注入的 now_fn；
        - key 在窗口内已有触发 → 抑制（返回 False，记 suppressed）；
        - 否则记为一次有效触发（返回 True，记 fired）。
        - window_seconds<=0 时永远放行（防风暴关闭）。
        """
        now = self._now_fn() if now is None else float(now)
        if self._window <= 0:
            self.log.append({"key": key, "ts": now, "action": ACTION_FIRED})
            return True
        last = self._last_fired.get(key)
        if last is not None and (now - last) < self._window:
            self.log.append({"key": key, "ts": now, "action": ACTION_SUPPRESSED})
            return False
        self._last_fired[key] = now
        self.log.append({"key": key, "ts": now, "action": ACTION_FIRED})
        return True

    def last_fired(self, key: str) -> float | None:
        """key 最近一次有效触发时间戳（epoch 秒），从未触发返回 None。"""
        return self._last_fired.get(key)

    def reset(self, key: str | None = None) -> None:
        """清空某 key（默认全部）的窗口状态与聚合日志。"""
        if key is None:
            self._last_fired.clear()
            self.log.clear()
        else:
            self._last_fired.pop(key, None)
            self.log = [e for e in self.log if e.get("key") != key]

    @property
    def window_seconds(self) -> float:
        return self._window


__all__ = [
    "DEFAULT_WINDOW_SECONDS",
    "ACTION_FIRED",
    "ACTION_SUPPRESSED",
    "EventWindowAggregator",
]
