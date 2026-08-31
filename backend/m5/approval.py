"""M5-M2 审批分级 + 偏离惯例确认（复用 perms 授权中心，不另造审批）。

审批分级：建议/确认/白名单自动（经 perms 常驻授权）。
偏离惯例确认：仅偏离时才确认（读 M1 惯例画像）；重建模式明说（M1.7 联动）。
主动 + 物理执行权限强制隔离（白名单外主动执行=0）。
"""
from __future__ import annotations

from typing import Any

AUTO = "auto"
CONFIRM = "confirm"
SUGGEST = "suggest"

REBUILD_NOTICE = "我在重新记你的新习惯"
DEVIATION_TEMPLATE = "这比平时不一样，确认不是手滑？"


class ApprovalGate:
    """审批分级闸门：复用 perms 授权中心，不另造审批。"""

    def __init__(self, perms: Any | None = None, habit_profile: Any | None = None) -> None:
        self._perms = perms                    # is_granted(category) -> bool
        self._habit_profile = habit_profile    # dict {mode, habits:[{id,status,...}]}

    def classify(self, category: str) -> str:
        """审批分级：白名单（常驻授权）→ auto；否则 confirm（白名单外不自动执行）。"""
        if self._perms is not None and self._perms.is_granted(category):
            return AUTO
        return CONFIRM

    def deviation_confirm(self, habit_id: str, observation: str) -> str | None:
        """偏离惯例确认：仅偏离（惯例 missed/broken）时返回确认话术；否则 None（不打扰）。"""
        if not self._is_deviation(habit_id):
            return None
        return DEVIATION_TEMPLATE

    def rebuild_notice(self) -> str | None:
        """重建模式明说（M1.7 联动）：重建期返回明说文案，否则 None。"""
        if self._habit_profile and self._habit_profile.get("mode") == "rebuild":
            return REBUILD_NOTICE
        return None

    def _is_deviation(self, habit_id: str) -> bool:
        if not self._habit_profile:
            return False
        for h in self._habit_profile.get("habits", []):
            if h.get("id") == habit_id:
                return h.get("status") in ("missed", "broken")
        return False


__all__ = [
    "ApprovalGate",
    "AUTO",
    "CONFIRM",
    "SUGGEST",
    "REBUILD_NOTICE",
    "DEVIATION_TEMPLATE",
]
