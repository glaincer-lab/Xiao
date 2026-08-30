"""跨模块语义化事件总线（M0，EVENT_REGISTRY 的实施层）。

与 `backend/session/state.py` 的 EventBus 分工：
- 那个 EventBus 是「给前端 WebSocket 的事件流」（state / assistant_result 等），
  只把会话状态喊给界面显示；
- 本模块是「模块间通信的信道」——M0/M1/M2/... 之间按事件名发布/订阅，
  让两个模块无需互相认识、无需直接调用对方，达到解耦。

接口契约（写死，供各模块使用，对应 EVENT_REGISTRY.md §一 事件总表）：

    EVENT_TYPES            # 事件名白名单（单一来源，来自 EVENT_REGISTRY）
    bus = EventBus()       # 默认总线实例（模块级单例，线程安全）

    # 订阅：返回一个「取消订阅」的 callable，用完后应调用
    unsub = bus.on("memory.profile_updated", handler)   # handler(payload: dict)
    # 发布：把 payload 广播给所有订阅了该事件名的 handler
    bus.emit("memory.profile_updated", {"version": "v1", "changed": ["content"]})

设计要点（AGENTS.md）：
- M0 只做「基础设施 + 事件名注册表 + 按类型过滤订阅」；**不做 payload schema 校验**
  （具体校验交给各订阅模块自己处理），避免 M0 大而全拖慢 MVP。
- 事件名一律字符串（`module.event` 点分）；发布端写错名 → 立刻抛 ValueError（fail-fast，
  日志指向 EVENT_REGISTRY），绝不静默吞掉连不上的事件。
- 仅标准库，依赖零新增；线程安全（与 state.py 相同的 Lock 模式）。

仅供标准库；MIT。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

# 事件名白名单（单一来源）：来自 docs/specs/EVENT_REGISTRY.md §一 事件总表（32 条）。
# 若在 EVENT_REGISTRY 中新增事件，必须同步补在这里，否则对该事件的订阅/发布会立刻报错。
# 【已冻结 · T0/S1】事件名白名单由 docs/specs/EVENT_REGISTRY.md §一 唯一决定。
# 新增事件：先登记 event registry，再补入本白名单；已发布事件禁止改名/改载荷。
EVENT_TYPES: frozenset[str] = frozenset({
    # ---- M0 横切基建 ----
    "macro.state_changed",        # {前态,后态,时长}  M0 → M3/M2/M6
    "attention.fullscreen",       # {on/off,进程名}  M0 → M3
    "attention.sigh",             # {置信,键鼠活跃}  M0 → M2（仅星云）
    "gateway.blocked",            # {词类,处置}      M0.2 → 日志
    "gateway.entities_found",     # [疑似人名]       M1.2 → M0.2/M1.5（实体回收）
    "gateway.obfuscate",          # {text}           M4 → M0.2
    "gateway.restore",            # {text}           M5 → M0.2
    "user.feedback",             # {目标,三态,原因}  M0 偏好引擎 → M1
    # ---- M1 记忆 ----
    "memory.profile_updated",     # {版本,变更字段}  M1 → M2（姿态缓存刷新）
    "memory.clarify_request",     # {条目id,二选一项}  M1 → 会话层（周配额）
    "memory.export_requested",    # {范围}           用户 → M6/M1
    # ---- M2 情感姿态 ----
    "affect.updated",             # {mood,intimacy,原因事件}  M2 → 前端/M3
    "posture.changed",            # {前态,后态,触发信号}       M2 → M1/前端
    "shadow.posture_decision",    # {会话id,决策,信号}         M2 → 影子日志
    # ---- M3 主动 ----
    "proactive.candidate",        # {类型,四维分,内容草案}  M3 内部 → 仲裁器
    "proactive.delivered",        # {id,用户响应}          M3 → M1/M3 画像
    "schedule.anniversary",       # {类型,事件}            M1/M3 → M3
    # ---- M4 视觉 ----
    "vision.conclusion",          # {场景,文字结论}  M4 → M1
    "vision.feedback",            # {三态}           M4 → M0 偏好引擎
    "vision.session_state",       # {session_id,state}  M4 → 前端/星云
    # ---- M5 物理 ----
    "device.command",             # {target,action,approval_level}  M5 → 执行器
    "device.state_changed",       # {entity,前后态}  M5/M0.3 → M1/M3
    "env.anomaly",                # {传感器,数值}  HA → M3/M2
    "plan.landed",                # {方案,落地凭证}  M5 → M1/M6
    # ---- M6 成长 ----
    "growth.candidate",           # {事件,能力凭证}  任务层/M5 → M6
    "growth.canonized",           # {记录id,轨别}    M6 → M1
    "micro_request.asked",        # {类型,用户响应}  M6 → M1
    # ---- T7 任务编排（backend/orchestrator · 智慧大脑+高效工人，自研借鉴 HomeRail）----
    "task.node_planned",        # {task_id,node_id,seq,kind,summary,depends_on}  编排→可观测/下游
    "task.node_started",        # {task_id,node_id,seq,kind,role}              编排→可观测
    "task.node_completed",      # {task_id,node_id,seq,kind,output}            编排→下游节点/M6
    "task.node_failed",         # {task_id,node_id,seq,kind,error}             编排→可观测
    "task.node_data",           # {task_id,source_node,target_node,key,value}  编排→下游节点
    "task.completed",           # {task_id,result,node_count,failed_count}     编排→M6/M1/M3
})


# 事件持久化日志默认目录：基于 Path(__file__) 相对项目根定位，不写死本机绝对路径。
# 仅在事件持久化开关开启时使用（默认关闭，不改变现有 emit 行为）。
_DEFAULT_EVENT_LOG_DIR = Path(__file__).resolve().parent.parent / "logs" / "events"


class EventBus:
    """线程安全的按事件名发布/订阅总线。

    用法：
        bus = EventBus()
        unsub = bus.on("memory.profile_updated", lambda p: print(p))
        bus.emit("memory.profile_updated", {"version": "v1"})
        unsub()                       # 取消订阅
    """

    def __init__(
        self,
        persist: bool = False,
        max_events: int = 10000,
        log_dir: Path | str | None = None,
    ) -> None:
        self._handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._lock = threading.Lock()
        # ---- 可选事件持久化 + 有界队列（T4 止血）。默认关闭，不改变现有 emit 行为 ----
        self._persist: bool = bool(persist)
        self._max_events: int = max(1, int(max_events))
        self._log_dir: Path = Path(log_dir) if log_dir is not None else _DEFAULT_EVENT_LOG_DIR
        self._log_path: Path = self._log_dir / "events.jsonl"
        # 进程重启后从磁盘恢复近期事件（有界），供 replay() 重放。
        self._events: deque[dict[str, Any]] = deque(self._load_log())

    @staticmethod
    def _validate(event_type: str) -> None:
        """事件名必须在 EVENT_TYPES 白名单内；否则 fail-fast，防止拼写漂移导致事件静默断开。"""
        if event_type not in EVENT_TYPES:
            raise ValueError(
                f"未知事件名：{event_type!r}。请在 docs/specs/EVENT_REGISTRY.md 登记，"
                "并同步加入 backend/event_bus.py 的 EVENT_TYPES。"
            )

    def on(self, event_type: str, handler: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """订阅指定事件名；返回取消订阅的函数。handler 只接收 payload（dict）。"""
        self._validate(event_type)
        if not callable(handler):
            raise TypeError("handler 必须是可调用对象")
        with self._lock:
            self._handlers.setdefault(event_type, []).append(handler)

        def _unsub() -> None:
            with self._lock:
                bucket = self._handlers.get(event_type)
                if bucket and handler in bucket:
                    bucket.remove(handler)
                    if not bucket:
                        self._handlers.pop(event_type, None)

        return _unsub

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """发布事件，广播给所有订阅该事件名的 handler。

        payload 缺省为 {}（很多事件只需一个类型，不需要额外字段）。
        单个 handler 抛异常不影响其它 handler（与 state.py 一致）。
        """
        self._validate(event_type)
        data: dict[str, Any] = payload if payload is not None else {}
        with self._lock:
            if self._persist:
                self._append_event_locked(event_type, data)
            subs = list(self._handlers.get(event_type, ()))
        for fn in subs:
            try:
                fn(data)
            except Exception:  # noqa: BLE001
                pass  # 单个订阅者异常不影响其它订阅者

    def count(self, event_type: str) -> int:
        """某事件名的当前订阅者数量（测试 / 诊断用）。"""
        with self._lock:
            return len(self._handlers.get(event_type, ()))

    def clear(self) -> None:
        """清空全部订阅（测试用，运行时不要调用）。"""
        with self._lock:
            self._handlers.clear()



    # ---- 可选事件持久化 / 有界队列 / 崩溃重放（T4·P1 止血） ----

    def set_persistence(
        self,
        persist: bool,
        max_events: int | None = None,
        log_dir: Path | str | None = None,
    ) -> None:
        """运行时开关事件持久化与有界队列（默认关闭；开启后 emit 前先落盘）。

        仅在需要"崩溃后重放不丢事件 / 队列有上限"时由调用方显式开启；
        未开启时 emit 行为与旧版完全一致。

        Args:
            persist: 是否开启持久化。
            max_events: 可选地调整容量上限（丢弃最旧 + 记日志）。
            log_dir: 可选地切换日志目录（相对路径基于项目根定位）。
        """
        with self._lock:
            self._persist = bool(persist)
            if max_events is not None:
                self._max_events = max(1, int(max_events))
                self._trim_locked()
            if log_dir is not None:
                self._log_dir = Path(log_dir)
                self._log_path = self._log_dir / "events.jsonl"
                self._log_dir.mkdir(parents=True, exist_ok=True)

    def replay(self, event_type: str | None = None) -> list[dict[str, Any]]:
        """重放已持久化事件（崩溃恢复 / 审计用）；返回 ``[{ts,event,payload}, ...]``。

        ``event_type`` 为 None 时返回全部。仅反映当前有界缓冲中的事件——
        超限被丢弃的最旧事件已计入告警日志；未溢出的近段事件保证可重放。
        """
        with self._lock:
            recs = [dict(r) for r in self._events]
        if event_type is not None:
            recs = [r for r in recs if r["event"] == event_type]
        return recs

    def persisted_count(self) -> int:
        """当前持久化缓冲中的事件条数（测试 / 诊断用）。"""
        with self._lock:
            return len(self._events)

    # ---- 持久化内部实现 ----

    def _append_event_locked(self, event_type: str, payload: dict[str, Any]) -> None:
        """调用方须持有 self._lock。追加事件；超限丢最旧并记日志；原子落盘。"""
        record: dict[str, Any] = {"ts": time.time(), "event": event_type, "payload": payload}
        self._events.append(record)
        overflowed = self._trim_locked()
        self._write_log_locked()
        if overflowed:
            logging.getLogger(__name__).warning(
                "事件总线持久化队列溢出（上限 %d），已丢弃最旧事件: %s",
                self._max_events,
                event_type,
            )

    def _trim_locked(self) -> bool:
        """调用方须持有 self._lock。丢弃超出上限的最旧事件；返回是否发生了丢弃。"""
        dropped = False
        while len(self._events) > self._max_events:
            self._events.popleft()
            dropped = True
        return dropped

    def _write_log_locked(self) -> None:
        """调用方须持有 self._lock。原子写 JSONL（每行一个事件），保持文件与缓冲一致。"""
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._log_path.with_name(self._log_path.name + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for rec in self._events:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            os.replace(tmp, self._log_path)
        except OSError:
            logging.getLogger(__name__).exception(
                "事件总线持久化日志写入失败: %s", self._log_path
            )

    def _load_log(self) -> list[dict[str, Any]]:
        """从磁盘加载已持久化事件（进程重启后恢复近期事件）。损坏时跳过坏行并告警。"""
        try:
            with self._log_path.open("r", encoding="utf-8") as f:
                recs: list[dict[str, Any]] = []
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict) and "event" in obj:
                        recs.append(obj)
            if len(recs) > self._max_events:
                recs = recs[-self._max_events:]
            return recs
        except FileNotFoundError:
            return []
        except OSError:
            logging.getLogger(__name__).warning(
                "事件总线持久化日志读取失败: %s", self._log_path
            )
            return []

# 模块级默认总线实例：各模块在初始化时对其订阅，实现跨模块解耦。
bus = EventBus()


__all__ = ["EventBus", "EVENT_TYPES", "bus"]