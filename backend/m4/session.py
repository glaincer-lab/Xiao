"""M4-M1 观察会话框架：S0-S6 状态机 + 帧生命周期（内存驻留，会话结束焚毁）。

摄像头经 M0 授权中心 camera_enabled 门控（复用，不另造）；
帧只内存驻留、会话结束统一焚毁；单会话帧数 ≤24 硬上限；S2 就位前零出网。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("m4.session")

STATES: tuple[str, ...] = ("S0", "S1", "S2", "S3", "S4", "S5", "S6")
MAX_FRAMES: int = 24

# 合法状态转换白名单（fail-fast，非法转换 ValueError）
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "S0": {"S1"},
    "S1": {"S0", "S2"},
    "S2": {"S0", "S3"},
    "S3": {"S2", "S4"},
    "S4": {"S5"},
    "S5": {"S6"},
    "S6": set(),
}


class VisionSession:
    """观察会话：S0 需求识别 → S1 同意开启 → S2 开窗就位 → S3 观察互动 →
    S4 结束采集 → S5 建议生成 → S6 反馈闭环。帧内存驻留，结束焚毁。"""

    def __init__(
        self,
        session_id: str = "default",
        auth: Any | None = None,
        bus: Any | None = None,
        vlm: Any | None = None,
    ) -> None:
        self.session_id = session_id
        self._auth = auth   # 提供 is_granted("camera_enabled")
        self._bus = bus     # 提供 emit(event, payload)
        self._vlm = vlm     # 提供 __call__(text)（可注入 stub）
        self.state: str = "S0"
        self._frames: list[bytes] = []
        self._segments: list[dict] = []
        self._outbound_calls: int = 0
        self._emit_state()

    # ---- 摄像头授权（复用 authorization.camera_enabled，默认关） ----
    def _camera_enabled(self) -> bool:
        if self._auth is None:
            return False
        try:
            return bool(self._auth.is_granted("camera_enabled"))
        except Exception:  # noqa: BLE001  授权不可用视为关闭（fail-closed）
            return False

    # ---- 状态机 ----
    def transition(self, target: str) -> bool:
        """合法转换返回 True；授权未通过返回 False（不改变状态）；非法转换 ValueError。"""
        if target not in STATES:
            raise ValueError(f"未知状态: {target}")
        if target not in _ALLOWED_TRANSITIONS.get(self.state, set()):
            raise ValueError(f"非法转换: {self.state} -> {target}")
        # 授权门控：S1 -> S2 需要摄像头授权
        if self.state == "S1" and target == "S2" and not self._camera_enabled():
            return False
        self.state = target
        self._emit_state()
        return True

    # ---- 帧生命周期 ----
    def add_frames(self, frames: list[bytes]) -> bool:
        """S3 采帧入内存；超过 ≤24 硬上限则整体拒绝（返回 False）。"""
        if len(self._frames) + len(frames) > MAX_FRAMES:
            return False
        self._frames.extend(frames)
        return True

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    def clear(self) -> None:
        """会话结束统一焚毁：清空帧与段，内存/磁盘零残留。"""
        self._frames.clear()
        self._segments.clear()

    # ---- VLM（出网） ----
    def vlm_comment(self, text: str) -> str:
        """段末轻量 VLM 即时评论（可注入 stub）。S2 就位前零出网（不调 VLM）。"""
        if self.state in ("S0", "S1"):
            return ""
        self._outbound_calls += 1
        if self._vlm is not None:
            return self._vlm(text)
        return ""

    @property
    def outbound_calls(self) -> int:
        return self._outbound_calls

    # ---- S5/S6 事件发布（vision.* 已登记，只发布） ----
    def conclude(self, scene: str, conclusion: str) -> None:
        """S5 建议生成：发布 vision.conclusion {场景,文字结论}（唯一持久化产物）。"""
        if self.state != "S5":
            return
        if self._bus is not None:
            self._bus.emit("vision.conclusion", {"场景": scene, "文字结论": conclusion})

    def feedback(self, verdict: str) -> None:
        """S6 反馈闭环：发布 vision.feedback {三态}（接受/不接受/部分）。"""
        if self.state != "S6":
            return
        if verdict not in ("接受", "不接受", "部分"):
            return
        if self._bus is not None:
            self._bus.emit("vision.feedback", {"三态": verdict})

    def _emit_state(self) -> None:
        if self._bus is not None:
            self._bus.emit("vision.session_state", {"session_id": self.session_id, "state": self.state})


__all__ = ["VisionSession", "STATES", "MAX_FRAMES"]
