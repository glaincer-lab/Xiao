"""编排层事件名常量（本模块的单一事实源，对应 EVENT_REGISTRY §一 task.node_* 组）。

新增/改名事件的三处同步（缺一不可，见 docs/specs/EVENT_REGISTRY.md §三）：
    1. docs/specs/EVENT_REGISTRY.md §一 事件总表；
    2. backend/event_bus.py 的 EVENT_TYPES 白名单；
    3. 本文件（本模块发布端）。

设计思想受 [xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）启发，
自研实现：节点间数据传递复用现有事件总线（backend/event_bus.py），不引入新通信机制。

仅标准库。
"""

from __future__ import annotations

# 生命周期：规划器为每个节点广播一次
EVENT_NODE_PLANNED = "task.node_planned"
# 执行节点开始执行（worker 进入独立 context 前）
EVENT_NODE_STARTED = "task.node_started"
# 执行节点成功完成，output 为节点产物
EVENT_NODE_COMPLETED = "task.node_completed"
# 执行节点失败，error 为失败原因
EVENT_NODE_FAILED = "task.node_failed"
# 节点间数据传递：source_node 的产物流向 target_node
EVENT_NODE_DATA = "task.node_data"
# 整任务完成：result 为合成后的最终回复
EVENT_TASK_COMPLETED = "task.completed"

# 编排层「发布端事件全集」：供测试断言「本模块发布的事件均已登记」。
PUBLISHED_EVENTS: frozenset[str] = frozenset({
    EVENT_NODE_PLANNED,
    EVENT_NODE_STARTED,
    EVENT_NODE_COMPLETED,
    EVENT_NODE_FAILED,
    EVENT_NODE_DATA,
    EVENT_TASK_COMPLETED,
})
