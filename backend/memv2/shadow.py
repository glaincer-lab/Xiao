"""backend.memv2.shadow —— M2 影子日志（真路由上线前只记录、绝不切换姿态）。

接口契约（写死，见 M2-D.phrases-shadow.md / M2_CONTRACT「影子日志」）：

    ShadowLog.record(session_id, decision, signals, score) -> None
    ShadowLog.get_entries() -> list[dict]

纪律（写死，DoD 断言）：
1. **只记录不切换**：``record()`` 的唯一职责是追加一条记录并（可选）广播
   ``shadow.posture_decision`` 事件。它**绝不**调用任何姿态切换接口，也**绝不**
   读取/改写当前姿态（``posture_state.current``）。本类不 import、不持有、不触发
   任何 posture 状态控制器——这是结构性的「无姿态副作用」。
2. **无姿态副作用断言**：构造函数可选注入一个 ``posture_controller``（仅作守卫）。
   ``record()`` 每次调用都会先跑 ``_assert_passive()``——若该控制器被调用过
   （即发生了姿态切换），立即抛 ``AssertionError`` 锁死。测试注入 spy 即可验证。
3. 记录字段：会话 id / 触发信号 / 信号组合得分 / 判定结果 / 时间戳（ISO 8601 UTC）。

另外：可选注入后端 ``bus``（duck-typed，须有 ``emit(name, payload)``），
``record()`` 成功追加后广播 ``shadow.posture_decision``，payload 与事件契约一致。
仅标准库；MIT。
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

# 事件名（与 backend/event_bus.EVENT_TYPES 白名单一致；注入的 bus 自行校验）
EVENT_POSTURE_DECISION = "shadow.posture_decision"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ShadowLog:
    """影子日志：只记录判定结果，不切换姿态。

    用法::

        log = ShadowLog()                      # 纯内存
        log.record("s-001", "friend", {"emotion_word": 1}, 0.72)
        entries = log.get_entries()

        # 接入事件总线（可选）：仅广播，仍不切换姿态
        log = ShadowLog(bus=bus)
    """

    #: 单条记录无上限时设为 0；给默认值防止长跑内存膨胀（保留最近 N 条）
    DEFAULT_MAX_ENTRIES = 10_000

    def __init__(self, *, bus: Any | None = None, posture_controller: Any | None = None,
                 max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._bus = bus
        self._posture_controller = posture_controller
        self._posture_controller_hits = 0
        self._max_entries = int(max_entries)
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    # -- 公共契约 ---------------------------------------------------------- #
    def record(self, session_id: str, decision: str, signals: dict[str, Any], score: float,
               ) -> None:
        """追加一条影子日志记录；无姿态副作用（绝不切换姿态）。

        - ``session_id``：会话标识。
        - ``decision``  ：判定结果（目标姿态名，如 ``friend``）。
        - ``signals``   ：触发信号（dict，如 ``{"emotion_word": 1}``）。
        - ``score``     ：信号组合得分（float）。

        返回 None；会后追加记录、并（若注入 bus）广播 ``shadow.posture_decision``。
        """
        # 结构性守卫：断言本轮没有任何姿态切换发生（否则因副作用而中断）。
        self._assert_passive()

        entry = {
            "session_id": str(session_id),
            "decision": str(decision),
            "signals": dict(signals),
            "score": float(score),
            "timestamp": _utc_now_iso(),
        }
        with self._lock:
            self._entries.append(entry)
            if self._max_entries and len(self._entries) > self._max_entries:
                # 保留最近 max_entries 条（真路由上线前影子期足够；防长跑内存膨胀）
                self._entries = self._entries[-self._max_entries:]

        if self._bus is not None:
            # 只广播「已判决」，供影子日志消费者收拢；绝不触发姿态切换。
            self._bus.emit(EVENT_POSTURE_DECISION, {
                "session_id": str(session_id),
                "decision": str(decision),
                "signals": dict(signals),
            })
        return None

    def get_entries(self) -> list[dict[str, Any]]:
        """返回全部记录（拷贝），不暴露内部列表引用。"""
        with self._lock:
            return [dict(e) for e in self._entries]

    def count(self) -> int:
        """已记录条数。"""
        with self._lock:
            return len(self._entries)

    # -- 无姿态副作用守卫 ---------------------------------------------------- #
    def _assert_passive(self) -> None:
        """断言注入的 posture_controller 从未被调用过；被调用即说明发生了姿态切换。

        这是「只记录不切换」的代码级锁：只要本类在任何记录流程里调用了姿态控制器，
        这里就会抛 ``AssertionError`` 让缺陷立刻暴露（而非静默切换）。
        """
        if self._posture_controller is not None and self._posture_controller_hits:
            raise AssertionError(
                "ShadowLog.record() 产生了姿态副作用：posture_controller 被调用过。"
                "影子日志必须只记录、不切换姿态。"
            )

    # 仅供外部（测试）观察的守卫计数；本类内部不调用 posture_controller。
    def _mark_posture_controller_hit(self) -> None:
        """由外部 spy 通过注入回调上报「被调用」——用于断言 record() 未触发它。"""
        self._posture_controller_hits += 1
