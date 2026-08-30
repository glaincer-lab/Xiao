"""编排层核心引擎（T7 · 介于 router 与 DSH 之间的任务编排层）。

设计思想受 [xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）启发，
自研实现（不抄代码）：「贵模型规划（planner）+ 廉价模型执行（worker）」分层，
per-node 独立 context，节点间数据经 backend/event_bus.py 的 task.node_* 事件传递，
不引入新通信机制；不使用 agent 核心循环（backend/agent.py _run 维持单 agent 单循环）。

仅标准库。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from backend.config import config
from backend.event_bus import EventBus, bus
from backend.llm.base import LLMClient
from backend.llm.factory import build_llm, build_llm_by_id

from backend.orchestrator.xiao_events import (
    EVENT_NODE_COMPLETED,
    EVENT_NODE_DATA,
    EVENT_NODE_FAILED,
    EVENT_NODE_PLANNED,
    EVENT_NODE_STARTED,
    EVENT_TASK_COMPLETED,
    PUBLISHED_EVENTS,
)
from backend.orchestrator.xiao_errors import XiaoNodeError, XiaoOrchestrationError, XiaoPlanError
from backend.orchestrator.xiao_models import XiaoNode, XiaoPlan, XiaoResult
from backend.orchestrator.xiao_prompt import (
    build_planner_messages,
    build_worker_messages,
    parse_plan,
)

logger = logging.getLogger(__name__)

# 编排层可直接构造的 LLMClient 抽象（供依赖注入/测试）。
# 默认从 backend.llm.factory 的 build_llm / build_llm_by_id 解析，复用多方案可切能力。
DEFAULT_MAX_NODES = 8


def resolve_role_client(role: str, cfg: Any = None) -> LLMClient:
    """按「角色（planner/worker）」解析一个 LLMClient：贵模型规划、廉价模型执行。

    优先级（复用 llm 多方案可切能力）：
      1. 配置 orchestrator.<role>_scheme 指向的 llm.models[].id；
      2. worker 未配置时回退到名为 "ollama" 的方案（本地廉价）；
      3. 以上均未命中时回退到当前激活方案 build_llm()（开箱可用）。
    """
    cfg = cfg if cfg is not None else config
    if role not in ("planner", "worker"):
        raise ValueError(f"未知编排角色: {role!r}")

    scheme = cfg.get(f"orchestrator.{role}_scheme")
    if scheme:
        try:
            return build_llm_by_id(str(scheme))
        except ValueError:
            logger.warning("orchestrator.%s_scheme=%r 未命中 llm.models 方案，回退。", role, scheme)

    if role == "worker":
        try:
            return build_llm_by_id("ollama")
        except ValueError:
            logger.warning("未配置 orchestrator.worker_scheme，且无 llm.models 的 ollama 方案，回退当前激活模型。")

    return build_llm()


class XiaoOrchestrator:
    """把复杂任务拆成「规划/执行」节点，各节点独立 context。

    用法（依赖注入便于测试；生产默认解析角色模型）：
        orch = XiaoOrchestrator()
        result = await orch.run("把本周渠道周报整理成三句话摘要")
    """

    def __init__(
        self,
        planner: LLMClient | None = None,
        worker: LLMClient | None = None,
        event_bus: EventBus | None = None,
        cfg: Any = None,
        max_nodes: int | None = None,
        task_id_factory: Any = None,
    ) -> None:
        self._planner = planner if planner is not None else resolve_role_client("planner", cfg)
        self._worker = worker if worker is not None else resolve_role_client("worker", cfg)
        self._bus = event_bus if event_bus is not None else bus
        self._cfg = cfg if cfg is not None else config
        self._max_nodes = int(max_nodes) if max_nodes else DEFAULT_MAX_NODES
        # task_id 工厂：默认 uuid（测试可注入确定性 id）
        self._task_id_factory = task_id_factory or (lambda: uuid.uuid4().hex[:12])
        # 每个 run 内的节点产物存储（供节点间取数；bus 作为对外信道）
        self._store: dict[str, str] = {}
        self._last_plan: XiaoPlan | None = None

    async def run(self, task_text: str, task_id: str | None = None) -> XiaoResult:
        """执行一次编排：规划 → 逐节点执行（per-node 独立 context）→ 合成结果。"""
        if not task_text or not str(task_text).strip():
            raise XiaoOrchestrationError("任务描述不能为空")
        task_id = task_id or self._task_id_factory()
        self._store = {}

        # 1) 贵模型规划
        plan = await self._plan(task_text, task_id)
        self._last_plan = plan
        if not plan.nodes:
            raise XiaoPlanError("规划器未产出任何执行节点")
        if len(plan.nodes) > self._max_nodes:
            logger.warning("规划超过上限 %d，截断前 %d 个。", self._max_nodes, self._max_nodes)
            plan.nodes = plan.nodes[: self._max_nodes]

        # 2) 排序（拓扑，尊重 depends_on）并逐节点执行
        ordered = self._topo_order(plan.nodes)
        failed = 0
        for node in ordered:
            node.inputs = self._gather_inputs(node)
            self._emit(EVENT_NODE_STARTED, {
                "task_id": task_id, "node_id": node.node_id, "seq": node.seq,
                "kind": node.kind.value, "role": "worker",
            })
            try:
                output = await self._run_node(task_text, plan, node)
                node.output = output or ""
                node.status = "done"
                self._store[node.node_id] = node.output
                self._emit(EVENT_NODE_COMPLETED, {
                    "task_id": task_id, "node_id": node.node_id, "seq": node.seq,
                    "kind": node.kind.value, "output": node.output,
                })
                self._fan_out_data(task_id, node, plan.nodes)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                node.status = "failed"
                node.error = str(exc)
                logger.exception("编排节点 %s 失败。", node.node_id)
                self._emit(EVENT_NODE_FAILED, {
                    "task_id": task_id, "node_id": node.node_id, "seq": node.seq,
                    "kind": node.kind.value, "error": str(exc),
                })
                # 失败节点不中断整任务：下游依赖可见失败，继续执行其余节点

        # 3) 合成最终结果并广播完成
        final = self._assemble(task_text, plan)
        result = XiaoResult(
            task_id=task_id,
            node_count=len(plan.nodes),
            failed_count=failed,
            output=final,
            nodes={n.node_id: n for n in plan.nodes},
        )
        self._emit(EVENT_TASK_COMPLETED, {
            "task_id": task_id, "result": final,
            "node_count": result.node_count, "failed_count": result.failed_count,
        })
        return result

    # ---- 规划 ----------------

    async def _plan(self, task_text: str, task_id: str) -> XiaoPlan:
        try:
            messages = build_planner_messages(task_text, self._max_nodes)
            completion = await self._planner.complete(messages)
            content = (completion.content or "")
        except Exception as exc:  # noqa: BLE001
            raise XiaoPlanError(f"规划器调用失败: {exc}") from exc
        try:
            plan = parse_plan(content, task_id)
        except ValueError as exc:
            raise XiaoPlanError(str(exc)) from exc
        for node in plan.nodes:
            self._emit(EVENT_NODE_PLANNED, {
                "task_id": task_id, "node_id": node.node_id, "seq": node.seq,
                "kind": node.kind.value, "summary": node.summary,
                "depends_on": list(node.depends_on),
            })
        return plan

    # ---- 执行 (per-node 独立 context) ----

    async def _run_node(self, task_text: str, plan: XiaoPlan, node: XiaoNode) -> str:
        """构建本节点全新消息序列并交给执行器（独立 context，不共享历史）。"""
        messages = build_worker_messages(task_text, plan.summary, node, node.inputs)
        try:
            completion = await self._worker.complete(messages)
        except Exception as exc:  # noqa: BLE001
            raise XiaoNodeError(node.node_id, str(exc)) from exc
        return (completion.content or "").strip()

    # ---- 节点间数据（bus 信道 + 内部 store）----

    def _gather_inputs(self, node: XiaoNode) -> dict:
        """收集本节点依赖的上游产物（取自内部 store，与 bus 上广播的 node_data 一致）。"""
        inputs: dict = {}
        for dep in node.depends_on:
            val = self._store.get(dep)
            if val is not None:
                inputs[dep] = val
        return inputs

    def _fan_out_data(self, task_id: str, node: XiaoNode, nodes: list[XiaoNode]) -> None:
        """本节点完成后，把产物经 task.node_data 广播给依赖它的下游节点。"""
        for nxt in nodes:
            if node.node_id in nxt.depends_on:
                self._emit(EVENT_NODE_DATA, {
                    "task_id": task_id,
                    "source_node": node.node_id,
                    "target_node": nxt.node_id,
                    "key": node.node_id,
                    "value": node.output,
                })

    # ---- 工具 ----

    def _topo_order(self, nodes: list[XiaoNode]) -> list[XiaoNode]:
        """按 depends_on 拓扑排序（仅在 plan 节点内索引；环检测）。"""
        node_map = {n.node_id: n for n in nodes}
        order: list[XiaoNode] = []
        done: set[str] = set()
        temp: set[str] = set()

        def visit(nid: str) -> None:
            if nid in done:
                return
            if nid in temp:
                raise XiaoOrchestrationError(f"规划节点存在循环依赖: {nid}")
            temp.add(nid)
            for dep in node_map[nid].depends_on:
                if dep in node_map:
                    visit(dep)
            temp.discard(nid)
            done.add(nid)
            order.append(node_map[nid])

        for n in nodes:
            visit(n.node_id)
        return order

    def _assemble(self, task_text: str, plan: XiaoPlan) -> str:
        """把各节点结果合成为面向用户的最终回复（简洁摘要）。"""
        lines: list[str] = []
        for node in plan.nodes:
            if node.status == "failed":
                lines.append(f"- [{node.node_id}] {node.summary}（失败）")
            else:
                lines.append(f"- [{node.node_id}] {node.summary}：{node.output or "（无结果）"}")
        head = plan.summary or task_text
        return f"{head}\n" + "\n".join(lines)

    def _emit(self, event_type: str, payload: dict) -> None:
        """经事件总线广播；事件名不合法时 fail-fast（EVENT_TYPES 白名单保证）。"""
        self._bus.emit(event_type, payload)


__all__ = [
    "XiaoOrchestrator",
    "resolve_role_client",
    "DEFAULT_MAX_NODES",
    "PUBLISHED_EVENTS",
    "XiaoNodeError",
    "XiaoOrchestrationError",
    "XiaoPlanError",
]