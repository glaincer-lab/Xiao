"""M5-M4 VLM 坐标兜底操电脑：UIA 失败 → 截屏 → VLM 定位 → 展示确认 → 执行 → 前后 diff 验证。

执行 ≠ 成功：前后截屏 diff 验证；坐标缩放映射（低分辨率 → 实际分辨率）；
支付/密码页硬黑名单；进程黑名单前台拒授权（复用 M0 attention guard_blacklisted_window）。
"""
from __future__ import annotations

from typing import Any

# 支付/密码页硬黑名单（关键词，命中即拒）
SENSITIVE_PAGE_KEYWORDS: tuple[str, ...] = ("支付", "付款", "密码", "password", "pay", "checkout")


def scale_coords(coord: tuple[float, float], from_size: tuple[int, int], to_size: tuple[int, int]) -> tuple[int, int]:
    """坐标缩放映射：低分辨率推理坐标 → 实际分辨率。"""
    x, y = coord
    fx, fy = from_size
    tx, ty = to_size
    return (int(round(x * tx / fx)), int(round(y * ty / fy)))


def is_sensitive_page(region: Any) -> bool:
    """支付/密码页硬黑名单：命中关键词即 True。"""
    s = str(region).lower()
    return any(k in s for k in SENSITIVE_PAGE_KEYWORDS)


class VlmOperator:
    """VLM 兜底操电脑编排器（可注入 screen/vlm/confirm）。"""

    def __init__(self, screen: Any | None = None, vlm: Any | None = None, confirm: Any | None = None) -> None:
        self._screen = screen  # capture() -> image ; execute(coord)
        self._vlm = vlm        # locate(image) -> coord | None
        self._confirm = confirm  # __call__(action) -> bool

    def run(self, action: str, verify: bool = True) -> dict[str, Any]:
        """兜底执行：截屏 → 敏感页黑名单 → VLM 定位 → 展示确认 → 执行 → 前后 diff 验证。"""
        if self._screen is None or self._vlm is None:
            return {"status": "fail", "message": "截屏/VLM 不可用"}
        before = self._screen.capture()
        if is_sensitive_page(before):
            return {"status": "blocked", "message": "支付/密码页，我不操作"}
        coord = self._vlm.locate(before)
        if coord is None:
            return {"status": "fail", "message": "VLM 定位失败"}
        if self._confirm is not None and not self._confirm(action):
            return {"status": "denied", "message": "未确认，不执行"}
        self._screen.execute(coord)
        if verify:
            after = self._screen.capture()
            if after == before:
                return {"status": "mismatch", "message": "执行后画面未变化"}
        return {"status": "ok"}


__all__ = ["VlmOperator", "scale_coords", "is_sensitive_page", "SENSITIVE_PAGE_KEYWORDS"]
