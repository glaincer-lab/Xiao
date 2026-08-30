"""M3-M1 预算制候选消费：每日额度预算（backend/m3/budget.py）。

实现 M3-proactive.md §3 的 proactive_budget {daily_quota, consumed_today} 与
§7 验收断言「日额度硬上限」：任何路径消费不得超过 daily_quota（消费前检查、
消费后累计），且跨天边界自动把 consumed_today 归零。

持久化：自动落盘 + 读回，采用原子写（tmp + os.replace），路径基于 __file__
相对项目根定位到 runtime/（可配置注入），不写死本机绝对路径。

设计口径（本文件写死）：
- daily_quota 默认 3（滑块可调 set_daily_quota()），钳制为 >=0（0 表示当日禁止主动）。
- consumed_today 随日期变更自动归零（day_key 存「消费发生日」）。
- 线程安全：consume/can_consume/剩余额度均在内部锁下执行，保证并发下不超限。

仅供标准库；MIT。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

DEFAULT_DAILY_QUOTA: int = 3
DEFAULT_PERSIST_FILENAME: str = "proactive_budget.json"


class QuotaExceededError(Exception):
    """当日剩余额度不足，拒绝本次消费。"""


def _default_persist_path() -> Path:
    """持久化路径：基于 __file__ 相对项目根定位到 runtime/，不写死本机绝对路径。"""
    return Path(__file__).resolve().parent.parent.parent / "runtime" / DEFAULT_PERSIST_FILENAME


def _day_key(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


class ProactiveBudget:
    """每日主动消费预算：额度上限 + 已消费计数，自动落盘与跨天重置。

    用法：
        b = ProactiveBudget(daily_quota=3)
        b.can_consume(1)   # 消费前预检
        b.consume(1)       # 消费（超过额度抛 QuotaExceededError）
        b.save()           # 主动落盘（consume/set_daily_quota 也会自动落盘）
    """

    def __init__(
        self,
        daily_quota: int | None = None,
        persist_path: str | Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._now_fn: Callable[[], datetime] = now_fn or datetime.now
        self._persist_path: Path = Path(persist_path) if persist_path else _default_persist_path()
        self._daily_quota: int = int(
            DEFAULT_DAILY_QUOTA if daily_quota is None else max(0, daily_quota)
        )
        self._consumed_today: int = 0
        self._day_key: str = _day_key(self._now_fn())
        self._load()  # 有落盘则读回，覆盖构造默认
        self._rollover_if_new_day()  # 跨天则先归零

    # ---- 查询 ----
    @property
    def daily_quota(self) -> int:
        return self._daily_quota

    @property
    def consumed_today(self) -> int:
        with self._lock:
            self._rollover_if_new_day()
            return self._consumed_today

    @property
    def remaining(self) -> int:
        with self._lock:
            self._rollover_if_new_day()
            return max(0, self._daily_quota - self._consumed_today)

    @property
    def day_key(self) -> str:
        return self._day_key

    def can_consume(self, n: int = 1) -> bool:
        """消费前预检：剩余额度是否允许再消费 n 条。"""
        with self._lock:
            self._rollover_if_new_day()
            return n >= 0 and (self._consumed_today + n) <= self._daily_quota

    def set_daily_quota(self, value: int) -> None:
        """滑块可调：设置每日额度上限，钳制为 >=0（0 表示当日禁止主动）。"""
        value = max(0, int(value))
        with self._lock:
            self._daily_quota = value
            self._save_locked()

    def consume(self, n: int = 1) -> int:
        """消费 n 条额度；超过上限抛 QuotaExceededError，保证任何路径不超 quota。"""
        with self._lock:
            self._rollover_if_new_day()
            if n < 0:
                raise ValueError("消费量 n 不能为负")
            if self._consumed_today + n > self._daily_quota:
                raise QuotaExceededError(
                    f"当日额度不足：已消费 {self._consumed_today}/{self._daily_quota}，"
                    f"再消费 {n} 将超上限。"
                )
            self._consumed_today += n
            self._save_locked()
            return self._consumed_today

    def reset_today(self) -> None:
        """手动归零当日已消费（供维护/测试；日常跨天自动处理）。"""
        with self._lock:
            self._consumed_today = 0
            self._day_key = _day_key(self._now_fn())
            self._save_locked()

    def save(self) -> None:
        with self._lock:
            self._save_locked()

    # ---- 内部 ----
    def _rollover_if_new_day(self) -> None:
        today = _day_key(self._now_fn())
        if today != self._day_key:
            self._day_key = today
            self._consumed_today = 0
            self._save_locked()

    def _save_locked(self) -> None:
        """原子写：tmp + os.replace；落盘失败不阻断内存态（打印告警）。调用方须持有锁。"""
        data = {
            "daily_quota": self._daily_quota,
            "consumed_today": self._consumed_today,
            "day_key": self._day_key,
        }
        path = self._persist_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError as e:  # noqa: BLE001
            print(f"[m3.budget] 预算落盘失败：{e}（内存态仍生效）")

    def _load(self) -> None:
        path = self._persist_path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._daily_quota = max(0, int(data.get("daily_quota", self._daily_quota)))
            self._consumed_today = max(0, int(data.get("consumed_today", 0)))
            self._day_key = str(data.get("day_key", self._day_key))
        except (json.JSONDecodeError, KeyError, ValueError) as e:  # noqa: BLE001
            print(f"[m3.budget] 预算文件损坏，已恢复默认：{path}（原因：{e}）")
