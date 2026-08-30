"""backend.memv2.bridge —— M2 姿态/情感事件接入跨模块事件总线（M2-C）。

把 M2 的姿态与情感判定接入跨模块事件总线（``backend/event_bus``）：

- 发布 ``posture.changed`` / ``affect.updated`` / ``shadow.posture_decision``；
- 订阅 ``memory.profile_updated``，刷新 **M1 画像只读快照** 缓存。

耦合纪律（写死，见 M2_CONTRACT.md / M2-heart.md §6）：
- **读 M1 画像走只读快照**：缓存，事件刷新，不实时联查。画像的数据来源由生产接线层经
  ``set_profile_provider`` 注入（无参回调，返回画像 dict）；本模块**不 import、不持有、
  不触碰 M1 的任何实现**（含内部函数），因此不直调 M1 内部。
- **事件名一律在 ``backend.event_bus.EVENT_TYPES`` 白名单内**：发布前去重校验
  （fail-fast），避免拼写漂移导致事件静默断开；与 ``event_bus`` 的白名单单一来源保持一致。
- ``score``（信号组合得分）按 M2-heart §6 / M2-D ``shadow.py`` 约定只入**影子记录**，
  **不入** ``shadow.posture_decision`` 事件 payload（事件契约 = {会话id,决策,信号}）。

仅标准库；MIT。
"""
from __future__ import annotations

import copy
import threading
from typing import Any, Callable, Optional

from backend.event_bus import EVENT_TYPES, bus as _default_bus

# --------------------------------------------------------------------------- #
# 事件名（单一来源在 backend.event_bus.EVENT_TYPES；此处仅作模块内常量）
# --------------------------------------------------------------------------- #
EVENT_POSTURE_CHANGED = "posture.changed"
EVENT_AFFECT_UPDATED = "affect.updated"
EVENT_POSTURE_DECISION = "shadow.posture_decision"
EVENT_PROFILE_UPDATED = "memory.profile_updated"

# --------------------------------------------------------------------------- #
# 模块级总线：默认指向 event_bus 单例；测试可替换为桩（duck-typed，须有 on/emit）。
# --------------------------------------------------------------------------- #
_bus: Any = _default_bus

# --------------------------------------------------------------------------- #
# M1 画像只读快照缓存
# --------------------------------------------------------------------------- #
_profile_snapshot: dict[str, Any] = {}
_profile_provider: Optional[Callable[[], dict[str, Any]]] = None
_snapshot_lock = threading.Lock()

# 订阅状态（幂等 —— 同一总线只订阅一次；可随 _bus 更换重绑）
_subscription: Optional[Callable[[], None]] = None
_subscribed_bus: Any = None


# --------------------------------------------------------------------------- #
# 校验：事件名必须在 EVENT_TYPES 白名单内（fail-fast，与 event_bus 语义一致）
# --------------------------------------------------------------------------- #
def _ensure_event(event_type: str) -> None:
    """事件名合法性守卫：不在白名单即刻抛 ValueError，防止静默断连。"""
    if event_type not in EVENT_TYPES:
        raise ValueError(
            f"未知事件名：{event_type!r}。请在 docs/specs/EVENT_REGISTRY.md 登记，"
            "并同步加入 backend/event_bus.py 的 EVENT_TYPES。"
        )


# --------------------------------------------------------------------------- #
# M1 画像只读快照（缓存 + 事件刷新，不实时联查）
# --------------------------------------------------------------------------- #
def set_profile_provider(provider: Callable[[], dict[str, Any]] | None) -> None:
    """注入 M1 画像来源（无参回调，返回画像 dict）。

    读取逻辑放在接线层（可安全 import M1 公开函数）；本模块只缓存其结果，不触碰
    M1 内部。设置后立即刷新一次快照，使缓存立即可用；此后由 ``memory.profile_updated``
    事件驱动刷新（见 ``init``）。

    ``provider`` 传 ``None`` 仅置空来源（不清缓存，等价于“暂时无新画像”）。
    """
    global _profile_provider
    _profile_provider = provider
    if provider is not None:
        refresh_profile()


def reset_profile_provider() -> None:
    """清空画像来源与快照缓存（回到“无画像可读”状态，测试 / 停用复位用）。"""
    global _profile_provider, _profile_snapshot
    _profile_provider = None
    with _snapshot_lock:
        _profile_snapshot = {}


def refresh_profile() -> dict[str, Any]:
    """从注入的来源**重读一次** M1 画像，写回快照缓存（事件驱动的刷新入口）。

    仅在来源变更/``memory.profile_updated`` 事件时调用；热点读取走
    ``get_profile_snapshot``（读缓存，绝不实时联查 M1）。无来源时快照为空。
    """
    global _profile_snapshot
    provider = _profile_provider
    snapshot: dict[str, Any] = {}
    if provider is not None:
        snapshot = dict(provider() or {})
    with _snapshot_lock:
        _profile_snapshot = snapshot
    return snapshot


def get_profile_snapshot() -> dict[str, Any]:
    """返回 M1 画像的**只读快照拷贝**。

    绝**不**实时联查 M1（只读缓存）；返回值是深拷贝，调用方改动不会污染缓存，
    保证下游拿到的是一份“只读快照”。
    """
    with _snapshot_lock:
        return copy.deepcopy(_profile_snapshot)


def _on_profile_updated(payload: dict[str, Any] | None) -> None:
    """``memory.profile_updated`` 订阅处理器：M1 画像已变，刷新快照缓存。"""
    refresh_profile()


# --------------------------------------------------------------------------- #
# 订阅 memory.profile_updated（幂等，可随 _bus 更换重绑）
# --------------------------------------------------------------------------- #
def init() -> Callable[[], None]:
    """订阅 ``memory.profile_updated``，刷新 M1 画像缓存；返回取消订阅函数。

    幂等：若已订阅**同一**总线实例则不重复注册；若 ``_bus`` 已更换，先退订旧的，
    再绑定到当前总线。生产接线：``set_profile_provider(...)`` 后调用一次本函数。
    """
    global _subscription, _subscribed_bus
    if _subscribed_bus is _bus and _subscription is not None:
        return _subscription
    if _subscription is not None:
        _subscription()
        _subscription = None
    _subscription = _bus.on(EVENT_PROFILE_UPDATED, _on_profile_updated)
    _subscribed_bus = _bus
    return _subscription


def shutdown() -> None:
    """退订 ``memory.profile_updated``（测试 / 停用复位用）。"""
    global _subscription, _subscribed_bus
    if _subscription is not None:
        _subscription()
        _subscription = None
        _subscribed_bus = None


# --------------------------------------------------------------------------- #
# 发布姿态/情感事件
# --------------------------------------------------------------------------- #
def _to_dict(value: Any) -> Any:
    """拷贝信号 dict，避免调用方后续改动污染已发布事件；非 dict 原样透传。"""
    if isinstance(value, dict):
        return dict(value)
    return value


def _affect_field(state: Any, name: str) -> int:
    """从 AffectState（dataclass 属性）或 dict 中读 mood/intimacy。"""
    if isinstance(state, dict):
        return int(state.get(name, 0))
    return int(getattr(state, name, 0))


def publish_posture_change(old: str, new: str, signal: dict[str, Any]) -> None:
    """发布 ``posture.changed``（{前态,后态,触发信号}）。

    - ``old`` / ``new``：姿态变化前/后（八态之一，如 ``friend`` → ``companion``）。
    - ``signal``：触发信号（dict，如 ``{"emotion_word": 1, "late_night": 1}``）。
    """
    _ensure_event(EVENT_POSTURE_CHANGED)
    _bus.emit(EVENT_POSTURE_CHANGED, {
        "previous": str(old),
        "current": str(new),
        "signal": _to_dict(signal),
    })


def publish_affect_updated(state: Any, reason: str) -> None:
    """发布 ``affect.updated``（{mood,intimacy,原因事件}）。

    - ``state``：``AffectState``（含 ``mood``/``intimacy``；兼容 dict）。
    - ``reason``：原因事件（如 ``praise`` / ``scold``）。
    """
    _ensure_event(EVENT_AFFECT_UPDATED)
    _bus.emit(EVENT_AFFECT_UPDATED, {
        "mood": _affect_field(state, "mood"),
        "intimacy": _affect_field(state, "intimacy"),
        "reason": str(reason),
    })


def log_shadow_decision(session_id: str, decision: str,
                        signals: dict[str, Any], score: float) -> None:
    """发布 ``shadow.posture_decision``（仅事件载荷 {会话id,决策,信号}）。

    ``score``（信号组合得分）按 M2-heart §6 / M2-D ``shadow.py`` 约定只入**影子记录**，
    **不**进入事件 payload（事件契约 = {会话id,决策,信号}）；此处保留该形参以对齐
    M2 影子判定工作流签名。
    """
    _ensure_event(EVENT_POSTURE_DECISION)
    _bus.emit(EVENT_POSTURE_DECISION, {
        "session_id": str(session_id),
        "decision": str(decision),
        "signals": _to_dict(signals),
    })


__all__ = [
    "EVENT_POSTURE_CHANGED",
    "EVENT_AFFECT_UPDATED",
    "EVENT_POSTURE_DECISION",
    "EVENT_PROFILE_UPDATED",
    "set_profile_provider",
    "reset_profile_provider",
    "refresh_profile",
    "get_profile_snapshot",
    "init",
    "shutdown",
    "publish_posture_change",
    "publish_affect_updated",
    "log_shadow_decision",
]
