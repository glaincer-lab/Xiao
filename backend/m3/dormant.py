"""M3-M4 DORMANT 订阅协调器（backend/m3/dormant.py）。

实现 M3-proactive.md sec 4.4 DORMANT 联动（M0 硬约束）与 sec 6 事件契约：
    订阅 macro.state_changed → 当后态 DORMANT 置 frozen（暂停主动源，供 M3-M2 heartbeat /
    M3-M3 event_trigger 在生成候选前检查，避免浪费）；RETURNING/ACTIVE → 解冻。
    只读宏态快照：is_proactive_allowed() / is_dormant() / regression_brief() 均为只读委托，
    由 M0 维护状态本身（M3-M4 不重做 DORMANT 状态机、不修 backend/macro_state.py）。

红线（写死，对应 M3-proactive.md 与 EVENT_REGISTRY）：
    - 本模块**不实现** DORMANT 状态机、**不修改** backend/macro_state.py；
    - 只订阅 + 只读宏态快照；跨模块走 event_bus（bus.on/emit），禁止直调其他模块核心函数；
    - **不新增事件、不改 EVENT_REGISTRY、不改 backend/event_bus.py 的 EVENT_TYPES 白名单**
      （macro.state_changed 已登记，只订阅）；
    - 零归因：本模块不生成「用户是不是讨厌我」类文本；零归因语义从 M0 读
      （regression_brief() 只读引用）；托付任务照办不推送由 M0 on_background_completion 处理，
      M3 侧绝不登记进展/不调用 M0 写方法。

衔接（与 M3-M2 / M3-M3）：DormantCoordinator 供 M3-M2 heartbeat / M3-M3 event_trigger
在生成候选前查询 is_frozen() 做「冻结早退」，避免冻结期仍生成候选再被 M3-M1 notify.process()
丢弃（DORMANT 已在生成候选前与消费端双层闸住，互补不重复）。

事件契约：订阅 macro.state_changed（已登记 EVENT_REGISTRY / event_bus.EVENT_TYPES）。

仅供标准库；MIT。
"""
from __future__ import annotations

import logging
from functools import partial
from typing import Any, Callable

# 只订阅这一个已登记事件（不新增、不改白名单）
SUBSCRIBED_EVENTS: tuple[str, ...] = ("macro.state_changed",)

# DORMANT 字符串常量引用（只读；来自 M0，安全默认非 DORMANT）
_DORMANT = "DORMANT"
_RETURNING = "RETURNING"
_ACTIVE = "ACTIVE"

logger = logging.getLogger("m3.dormant")


def _default_bus():
    from backend.event_bus import bus
    return bus


class DormantCoordinator:
    """DORMANT 订阅协调器：订阅 macro.state_changed，维护 frozen 供主动源查询。

    依赖可用关键字注入（便于测试替身；缺省懒加载真实单例）：
        bus    事件总线实例（默认全局 bus）
        macro  提供 is_proactive_allowed()/is_dormant()/regression_brief() 的宏态快照对象
              （默认 lazily backend.macro_state.macro_state 单例）
    """

    def __init__(self, bus: Any | None = None, macro: Any | None = None) -> None:
        self._bus = bus if bus is not None else _default_bus()
        self._macro = macro
        self._frozen: bool = False
        # 只读宏态快照：构造时读当前 macro 的 is_proactive_allowed() 定初值（若已 DORMANT 则冻结）
        self._load_snapshot()
        # 订阅 macro.state_changed（bus.on；事件名已在白名单，只订阅不新增）
        self._unsubs: list[Callable[[], None]] = []
        for evt in SUBSCRIBED_EVENTS:
            self._unsubs.append(self._bus.on(evt, partial(self._on_event, evt)))

    # =================================================================
    # 事件入口（bus.on handler）：后态为 DORMANT → 冻结；RETURNING/ACTIVE → 解冻
    # =================================================================
    def _on_event(self, event_type: str, payload: Any = None) -> bool:
        """macro.state_changed 到达：按「后态==DORMANT」更新 frozen（与 M0 语义对齐）。

        非 DORMANT（ACTIVE/IDLE/RETURNING）一律不冻结——IDLE 允许主动，
        与 M0 is_proactive_allowed()==(state!=DORMANT) 保持一致。
        零归因：本 handler 只改 frozen 布尔，绝不输出任何自我怀疑/抱怨文本。
        """
        data = payload or {}
        new_state = str(data.get("后态", data.get("state", "")))
        self._frozen = new_state == _DORMANT
        logger.info("[m3.dormant] macro.state_changed 后态=%s，frozen=%s", new_state, self._frozen)
        return self._frozen

    # =================================================================
    # 只读查询
    # =================================================================
    @property
    def frozen(self) -> bool:
        """当前是否冻结（只读）。"""
        return self._frozen

    def is_frozen(self) -> bool:
        """供 M3-M2 heartbeat / M3-M3 event_trigger 在生成候选前查询。"""
        return self._frozen

    # ---- 只读宏态快照委托（M0 只读接口，不修改 macroscopic_state） ----
    def is_dormant(self) -> bool:
        """只读：当前宏态是否 DORMANT（委托 M0）。"""
        return bool(self._get_macro().is_dormant())

    def proactive_allowed(self) -> bool:
        """只读：M0 主动引擎冻结闸门 is_proactive_allowed()。"""
        return bool(self._get_macro().is_proactive_allowed())

    def regression_brief(self) -> str:
        """只读：M0 回归简报（零归因）。本模块绝不登记进展（on_background_completion 由 M0 消费）。"""
        return str(self._get_macro().regression_brief())

    # =================================================================
    # 依赖懒加载（缺省懒加载 M0 单例，只读）
    # =================================================================
    def _get_macro(self):
        if self._macro is None:
            from backend.macro_state import macro_state
            self._macro = macro_state
        return self._macro

    def _load_snapshot(self) -> None:
        """只读宏态快照定初值：当前不允许主动（DORMANT）→ 初始即冻结。"""
        self._frozen = not bool(self._get_macro().is_proactive_allowed())

    def close(self) -> None:
        """取消全部订阅（运行时清理；通常不调用，协调器为长生命周期单例）。"""
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass
        self._unsubs.clear()


__all__ = [
    "SUBSCRIBED_EVENTS",
    "DormantCoordinator",
]
