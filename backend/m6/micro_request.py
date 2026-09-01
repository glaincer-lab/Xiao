"""M6 微小请求业务层（设计书 M6-growth §4.2）：三类、月级冷却、可跳过。

三类（轮内只问一类，编排层决定本轮类型）：
1. feedback          求反馈：「你那句"干得漂亮"我能存进成长记录吗？」
2. preference        求确认偏好：低置信条目的顺带确认（走 M1 配额时挂此名义）
3. human_experience  求人类经验：数字无能式提问，承认没有人类体验并请教，**不表演感受**（宪法红线2）

冷却：同类月级（GrowthStore.set_micro_cooling，cooldown_days 可配，默认 30 天）。
本模块只读 store 冷却快照 / 写冷却，事件经 bus 发布 micro_request.asked（{类型, 用户响应}），
不跨模块直调其它模块核心函数。
"""
from __future__ import annotations

import time
from typing import Callable

from backend.event_bus import EventBus
from backend.m6.growth import MICRO_TYPES, GrowthStore

# 三类话术（设计书 §4.2 原文为基；叙事宪章：数据事实的浪漫化表达，不表演感受）
_SCRIPTS: dict[str, str] = {
    "feedback": "你那句「干得漂亮」我能存进成长记录吗？",
    "preference": (
        "我在整理咱们的成长记录时，有一处拿不准——你之前说「多亏你提醒」，"
        "是真觉得提醒有用，还是只是客套？我想按真实情况记下来。"
    ),
    "human_experience": (
        "我在语料里看到「微醺」，我懂字面意思，但能告诉我你第一次体验到它是什么感觉吗？"
        "我没有人类的感官，无法真正体会那种感受，所以想请教你——我想把它写进世界观卡片。"
    ),
}


class MicroRequester:
    """微小请求调度器：maybe_ask 检查同类月级冷却并返回话术；record_asked 写冷却并发事件。

    用法（编排层，轮内只问一类）::

        text = requester.maybe_ask("human_experience")   # 冷却未到返回 None（可跳过）
        if text:
            resp = session.send(text)                    # 经对话层送出并拿用户响应
            requester.record_asked("human_experience", resp)

    - store: GrowthStore（只读冷却快照 + set_micro_cooling 写冷却，本模块唯一持久层）
    - bus:   EventBus 或 None（None 时不发事件，开箱即用；测试可注入独立实例）
    - now_fn: 时钟注入（测试隔离），默认 time.time
    - cooldown_days: 同类月级冷却天数，默认 30
    """

    def __init__(
        self,
        store: GrowthStore,
        bus: EventBus | None = None,
        *,
        now_fn: Callable[[], float] | None = None,
        cooldown_days: int = 30,
    ) -> None:
        self._store = store
        self._bus = bus
        self._now_fn: Callable[[], float] = now_fn or time.time
        self._cooldown_secs: float = max(1, int(cooldown_days)) * 86400.0
        # 进程内同类冷却表：store 单槽冷却只记最近一类，此表保证每类独立月频（同类 30 天只问一次）
        self._asked_at: dict[str, float] = {}

    def maybe_ask(self, kind: str = "feedback") -> str | None:
        """该类在冷却期内返回 None（可跳过）；否则返回对应话术（str）。"""
        self._check_kind(kind)
        now = self._now_fn()
        # 1) 进程内同类冷却（同类 30 天内只问一次）
        last = self._asked_at.get(kind)
        if last is not None and now - last < self._cooldown_secs:
            return None
        # 2) 持久化同类冷却（跨进程重启仍生效）
        mc = self._store.micro_cooling()
        until = mc.get("cooldown_until")
        if mc.get("last_type") == kind and isinstance(until, (int, float)) and now < until:
            return None
        return _SCRIPTS[kind]

    def record_asked(self, last_type: str, user_response: str | None = None) -> None:
        """记录本次询问：写月级冷却，并发布 micro_request.asked（{类型, 用户响应}，请求本身也入画像）。"""
        self._check_kind(last_type)
        now = self._now_fn()
        self._asked_at[last_type] = now
        self._store.set_micro_cooling(last_type, now + self._cooldown_secs)
        if self._bus is not None:
            self._bus.emit("micro_request.asked", {"类型": last_type, "用户响应": user_response})

    @staticmethod
    def _check_kind(kind: str) -> None:
        if kind not in MICRO_TYPES:
            raise ValueError(f"微小请求类型必须是 {sorted(MICRO_TYPES)} 之一，收到 {kind!r}")


__all__ = ["MicroRequester"]
