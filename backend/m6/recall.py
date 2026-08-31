"""M6-M1 回顾推送接入：回顾简报组合器（RecallComposer）。

回顾推送走 M3 心跳/notify 通道，受预算+总闸门约束（约束由推送调度层执行）；
被动问（"我最近怎么样"）随时可答、不受预算限制——本模块两种口径共用同一素材源。

素材为 GrowthStore 只读快照（user_records/agent_records/shared_memories，
双轨分离、时间倒序），本模块只组合不写入、不发布事件（事件白名单见 event_bus）。
呈现约定（设计书 §4.1/§5）：双轨分栏并列、时间倒序、无奖杯弹窗、翻看时才渲染；
叙事宪章：一切自我表达 = 数据事实的浪漫化表达，共同回忆用"咱们"。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from backend.m6.growth import GrowthStore

# 分栏键（与 GrowthStore 轨别对应；shared 为共同记忆栏）
TRACK_KEYS = ("user_track", "agent_track", "shared")

# 各轨展示字段白名单：投影隔离，保证双轨不混入对方字段
_USER_FIELDS = ("id", "ts", "date", "milestone", "source", "canon")
_AGENT_FIELDS = ("id", "ts", "date", "milestone", "capability_event", "canon")
_SHARED_FIELDS = ("id", "ts", "date", "event", "luminance")

# 单轨简报最多点名的条数（超出只报总数，避免刷屏）
_MAX_NAMED = 3


class RecallComposer:
    """回顾简报组合器：从 GrowthStore 读三源素材，产出分栏数据与被动问文案。

    now_fn 为推送调度预留的当前时间注入点（预算/冷却判断由调度层完成，
    本模块不在被动问口径上做任何限制）。
    """

    def __init__(self, store: GrowthStore, *, now_fn: Callable[[], float] | None = None) -> None:
        if not isinstance(store, GrowthStore):
            raise TypeError("RecallComposer 需要 GrowthStore 实例（见 backend/m6/growth.py）")
        self._store = store
        self._now_fn = now_fn if now_fn is not None else time.time

    def compose(self) -> dict[str, list[dict[str, Any]]]:
        """三源素材分栏快照（只读、时间倒序、字段投影隔离）。

        返回 {"user_track": [...], "agent_track": [...], "shared": [...]}：
        - user_track  用户轨：milestone/source/canon，无 capability_event
        - agent_track 小二轨：milestone/capability_event/canon，无 source
        - shared      共同记忆：event/luminance
        供呈现层翻看渲染（双轨分栏并列、无奖杯弹窗）。
        """
        return {
            "user_track": [self._project(r, _USER_FIELDS) for r in self._store.user_records()],
            "agent_track": [self._project(r, _AGENT_FIELDS) for r in self._store.agent_records()],
            "shared": [self._project(r, _SHARED_FIELDS) for r in self._store.shared_memories()],
        }

    def passive_answer(self) -> str:
        """被动问（"我最近怎么样"）随时可答：三源素材 + 数字事实浪漫化简报。

        不受任何预算/总闸门限制；无素材时给伙伴口吻的非空答复，绝不编造数据。
        """
        data = self.compose()
        user, agent, shared = data["user_track"], data["agent_track"], data["shared"]

        lines: list[str] = ["让我翻翻咱们的小本子。"]

        if user:
            named = "、".join(f"「{r['milestone']}」" for r in user[:_MAX_NAMED])
            lines.append(f"你这边记下了 {len(user)} 件事：{named}。")
        if agent:
            named = "、".join(f"「{r['milestone']}」" for r in agent[:_MAX_NAMED])
            lines.append(f"我也在长大，长出了 {len(agent)} 个新本事：{named}。")
        if shared:
            named = "、".join(f"「{r['event']}」" for r in shared[:_MAX_NAMED])
            lines.append(f"咱们一起的回忆有 {len(shared)} 段：{named}。")

        if len(lines) == 1:  # 素材全空：不编造，只给伙伴口吻的非空答复
            return "咱们共同的日子才刚刚开始，等小本子上攒下第一笔，我再慢慢讲给你听。"

        lines.append("都是实打实发生过的事，一句都没编。")
        return "\n".join(lines)

    @staticmethod
    def _project(record: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
        """字段白名单投影：只保留本轨展示字段，杜绝对方轨字段混入。"""
        return {k: record[k] for k in fields if k in record}


__all__ = ["RecallComposer", "TRACK_KEYS"]
