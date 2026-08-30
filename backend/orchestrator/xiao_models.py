"""编排层数据模型（T7 · 节点级对象）。

设计思想受 [xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）启发，
自研实现：per-node 独立 context 的「规划/执行」节点模型；数据模型完全自有（非照搬）。

仅标准库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class XiaoNodeKind(str, Enum):
    """节点类型。规划器产出计划后，每个节点由廉价模型执行。"""

    PLAN = "plan"          # 规划/评审节点（贵模型，少数场景复用）
    EXECUTE = "execute"    # 执行节点（廉价模型，主体工作单元）

    def __str__(self) -> str:  # noqa: D105
        return self.value


@dataclass
class XiaoNode:
    """一个可执行的编排节点。

    每个节点持有独立 context：执行时构建全新消息序列（系统 + 任务 + 节点摘要 + 上游数据），
    不共享、不累积上游会话历史 —— 这是「per-node 独立 context」的关键。
    """

    node_id: str                                  # 全局唯一节点 id（脚本可读，如 "n1"）
    seq: int                                      # 规划器给出的顺序号（稳定展示用）
    summary: str                                  # 该节点要完成的子任务的一句话摘要
    kind: XiaoNodeKind = XiaoNodeKind.EXECUTE     # 节点类型
    depends_on: list[str] = field(default_factory=list)  # 前置节点 id 列表
    inputs: dict = field(default_factory=dict)             # 执行时从上游节点收集的数据
    output: str = ""                              # 执行产物（该节点 context 的最终回复）
    error: str = ""                               # 失败原因（成功时为空）
    status: str = "pending"                       # pending | running | done | failed


@dataclass
class XiaoPlan:
    """规划结果：贵模型（planner）把复杂任务拆解出的节点列表。"""

    task_id: str
    summary: str                                  # 整任务一句话概括
    nodes: list[XiaoNode] = field(default_factory=list)


@dataclass
class XiaoResult:
    """整任务执行结果（对调用方/事件订阅者的输出）。"""

    task_id: str
    node_count: int                               # 规划的节点总数
    failed_count: int                             # 失败节点数
    output: str                                   # 合成后的最终回复（供用户/上层使用）
    nodes: dict[str, XiaoNode] = field(default_factory=dict)  # node_id -> 节点（含状态）

    @property
    def ok(self) -> bool:
        return self.failed_count == 0
