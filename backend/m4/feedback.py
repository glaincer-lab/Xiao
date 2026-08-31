"""M4-M4 反馈闭环：S6 三态（接受/不接受/部分）全入 M1 记忆；负反馈优先入画像。

边界：唯一持久化产物是 text_conclusions（文字结论入 M1）；帧不持久化。
拒绝仅追问一次原因，不答不追问。
"""
from __future__ import annotations

from typing import Any

VERDICTS: tuple[str, ...] = ("接受", "不接受", "部分")
MAX_REFUSAL_FOLLOWUP: int = 1


class FeedbackLoop:
    """S6 反馈闭环：三态全入记忆，负反馈标记负向（画像价值更高）。"""

    def __init__(self, memory: Any | None = None, bus: Any | None = None) -> None:
        self._memory = memory  # 提供 record_feedback(entry)（可注入）
        self._bus = bus        # 提供 emit(event, payload)
        self._refusal_followups: int = 0

    def record_feedback(self, verdict: str, conclusion: str = "") -> dict[str, Any]:
        """记录三态反馈到 M1 记忆；发布 vision.feedback 事件。"""
        if verdict not in VERDICTS:
            raise ValueError(f"非法三态: {verdict}")
        entry = {
            "三态": verdict,
            "文字结论": conclusion,
            "负向": verdict == "不接受",
        }
        if self._memory is not None:
            self._memory.record_feedback(entry)
        if self._bus is not None:
            self._bus.emit("vision.feedback", {"三态": verdict})
        return entry

    def should_followup_refusal(self) -> bool:
        """拒绝仅追问一次原因；不答不追问。"""
        if self._refusal_followups >= MAX_REFUSAL_FOLLOWUP:
            return False
        self._refusal_followups += 1
        return True


__all__ = ["FeedbackLoop", "VERDICTS", "MAX_REFUSAL_FOLLOWUP"]
