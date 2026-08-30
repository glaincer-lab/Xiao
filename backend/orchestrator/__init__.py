"""backend.orchestrator · 任务编排层（T7 · 智慧大脑 + 高效工人）。

设计思想受 [xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）启发，
自研实现：把复杂任务拆成「规划（贵模型）/执行（廉价模型）」节点，per-node 独立 context，
节点间数据经 backend/event_bus.py 的 task.node_* 事件传递，不引入新通信机制。
不与 HomeRail 代码混淆：命名全套 backend/orchestrator/ + xiao_ 前缀。
"""

from __future__ import annotations

from backend.orchestrator.xiao_core import (
    DEFAULT_MAX_NODES,
    PUBLISHED_EVENTS,
    XiaoOrchestrator,
    resolve_role_client,
)
from backend.orchestrator.xiao_errors import (
    XiaoNodeError,
    XiaoOrchestrationError,
    XiaoPlanError,
)
from backend.orchestrator.xiao_models import XiaoNode, XiaoNodeKind, XiaoPlan, XiaoResult
from backend.orchestrator.xiao_events import (
    EVENT_NODE_COMPLETED,
    EVENT_NODE_DATA,
    EVENT_NODE_FAILED,
    EVENT_NODE_PLANNED,
    EVENT_NODE_STARTED,
    EVENT_TASK_COMPLETED,
)

__all__ = [
    "XiaoOrchestrator",
    "resolve_role_client",
    "DEFAULT_MAX_NODES",
    "PUBLISHED_EVENTS",
    "XiaoNode", "XiaoNodeKind", "XiaoPlan", "XiaoResult",
    "XiaoPlanError", "XiaoNodeError", "XiaoOrchestrationError",
    "EVENT_NODE_PLANNED", "EVENT_NODE_STARTED", "EVENT_NODE_COMPLETED",
    "EVENT_NODE_FAILED", "EVENT_NODE_DATA", "EVENT_TASK_COMPLETED",
]