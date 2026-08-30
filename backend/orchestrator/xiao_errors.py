"""编排层异常定义（T7 · 智慧大脑 + 高效工人）。

设计思想受 [xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）启发，
但为小二（Xiao）自研实现：分层思想借鉴，数据模型 / 事件机制为自有。

仅标准库。
"""

from __future__ import annotations


class XiaoOrchestrationError(Exception):
    """编排层（backend/orchestrator/）内出现的可预期错误。

    例如：规划器未产出节点、节点依赖存在环、任务超出节点上限、节点执行失败。
    调用方可捕获它以做人类可读兜底，而非暴露裸堆栈。
    """


class XiaoPlanError(XiaoOrchestrationError):
    """规划器输出不可解析 / 未产出可执行节点。"""


class XiaoNodeError(XiaoOrchestrationError):
    """某执行节点失败（携带 node_id 与内部错误原因）。"""

    def __init__(self, node_id: str, reason: str) -> None:
        self.node_id = node_id
        self.reason = reason
        super().__init__(f"执行节点 {node_id!r} 失败：{reason}")
