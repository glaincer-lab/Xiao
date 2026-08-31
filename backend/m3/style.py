"""M3-M5 主动风格画像（纯行为学习，无心理标签）。

只学行为（回应率 / 信息密度偏好），不推断依恋类型、不存心理标签；
手动设置 override 后暂停该维度自适应。
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Callable, Mapping

try:  # 反向调节频率档（单一事实源来自 M3-M2 heartbeat）
    from backend.m3.heartbeat import FREQUENCY_LADDER as _LADDER
except Exception:  # noqa: BLE001  测试替身隔离时回退
    _LADDER = ("daily", "every_2_days", "every_3_days", "sparse")

WINDOW_DAYS: int = 30
DEFAULT_RESPONSE_RATE: float = 0.5
DEFAULT_DENSITY: str = "med"
_DENSITIES = ("low", "med")


class StyleProfile:
    """主动风格画像：字段严格三样，无心理标签。"""

    def __init__(
        self,
        now_fn: Callable[[], _dt.datetime] | None = None,
        density_source: Callable[[], str | None] | None = None,
    ) -> None:
        self._now_fn = now_fn or _dt.datetime.now
        self._density_source = density_source
        self._deliveries: list[_dt.datetime] = []
        self._responses: list[_dt.datetime] = []
        self._response_rate: float = DEFAULT_RESPONSE_RATE
        self._preferred_density: str = DEFAULT_DENSITY
        self._override: bool = False
        self._habit_profile: Mapping[str, Any] | None = None

    # ---- 三字段（只读属性） ----
    @property
    def response_rate(self) -> float:
        return self._response_rate

    @property
    def preferred_density(self) -> str:
        return self._preferred_density

    @property
    def override_by_user(self) -> bool:
        return self._override

    # ---- 只读 M1 画像快照 ----
    def bind_habit_profile(self, store: Any) -> Mapping[str, Any]:
        """只读 M1 惯例画像快照（habit_profile()），不写画像。"""
        self._habit_profile = store.habit_profile()
        return self._habit_profile

    # ---- 行为记录（override 时暂停自适应） ----
    def record_delivery(self, now: _dt.datetime | None = None) -> None:
        if self._override:
            return
        self._deliveries.append(now or self._now_fn())
        self._recompute()

    def record_response(self, now: _dt.datetime | None = None) -> None:
        if self._override:
            return
        self._responses.append(now or self._now_fn())
        self._recompute()

    def learn(self) -> None:
        """从注入密度源更新 density；override 时暂停。"""
        if self._override:
            return
        if self._density_source is not None:
            d = self._density_source()
            if d in _DENSITIES:
                self._preferred_density = d

    # ---- 反向调节心跳 ----
    def apply_to_heartbeat(self, heartbeat: Any) -> str:
        """回应率低(<0.3)→沿 FREQUENCY_LADDER 降一档；否则保持。只用公开 frequency 属性。"""
        if self._response_rate < 0.3 and getattr(heartbeat, "frequency", None) in _LADDER:
            idx = _LADDER.index(heartbeat.frequency)
            if idx < len(_LADDER) - 1:
                heartbeat.frequency = _LADDER[idx + 1]
        return getattr(heartbeat, "frequency", "")

    # ---- override ----
    def set_override(self) -> None:
        self._override = True

    def clear_override(self) -> None:
        self._override = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "response_rate": self._response_rate,
            "preferred_density": self._preferred_density,
            "override_by_user": self._override,
        }

    def fields(self) -> frozenset[str]:
        return frozenset(("response_rate", "preferred_density", "override_by_user"))

    # ---- 内部 ----
    def _recompute(self) -> None:
        now = self._now_fn()
        cutoff = now - _dt.timedelta(days=WINDOW_DAYS)
        d = [t for t in self._deliveries if t >= cutoff]
        r = [t for t in self._responses if t >= cutoff]
        self._response_rate = round(len(r) / len(d), 3) if d else DEFAULT_RESPONSE_RATE


__all__ = [
    "StyleProfile",
    "WINDOW_DAYS",
    "DEFAULT_RESPONSE_RATE",
    "DEFAULT_DENSITY",
]
