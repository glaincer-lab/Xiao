"""编排层（backend/orchestrator/）单元测试（T7 · 智慧大脑 + 高效工人）。

覆盖：拆复杂任务→规划/执行节点正确流转、per-node 独立 context、事件总线收发、
task.node_* 事件登记 + EVENT_TYPES 白名单同步、命名无 homerail_、plan 解析与拓扑。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from backend.event_bus import EVENT_TYPES, EventBus
from backend.llm.base import ChatMessage, Completion, LLMClient

from backend.orchestrator import (
    EVENT_NODE_COMPLETED,
    EVENT_NODE_DATA,
    EVENT_NODE_FAILED,
    EVENT_NODE_PLANNED,
    EVENT_NODE_STARTED,
    EVENT_TASK_COMPLETED,
    XiaoNode,
    XiaoOrchestrator,
    XiaoOrchestrationError,
    XiaoPlanError,
)
from backend.orchestrator.xiao_prompt import parse_plan

ROOT = Path(__file__).resolve().parent.parent
ORCH = ROOT / "backend" / "orchestrator"

ALL_NODE_EVENTS = (
    EVENT_NODE_PLANNED, EVENT_NODE_STARTED, EVENT_NODE_COMPLETED,
    EVENT_NODE_FAILED, EVENT_NODE_DATA, EVENT_TASK_COMPLETED,
)


class FakeLLM(LLMClient):
    """记录每次 complete 调用并按 responder 返回文本（不触网）。"""

    def __init__(self, responder):
        self.responder = responder
        self.calls: list[list[ChatMessage]] = []

    async def complete(self, messages, tools=None):
        self.calls.append(messages)
        return Completion(content=self.responder(messages))


PLAN_JSON = (
    '{"summary": "整理本周渠道周报", "nodes": ['
    '{"id": "n1", "summary": "汇总各代表处销量", "depends_on": []},'
    '{"id": "n2", "summary": "生成同比图表", "depends_on": ["n1"]},'
    '{"id": "n3", "summary": "提炼三句话摘要", "depends_on": ["n2"]}'
    ']}'
)


def _planner_responder(messages):
    return PLAN_JSON


def _worker_responder(messages):
    user = next((m.content for m in messages if m.role == "user"), "") or ""
    m = re.search(r"【本节点\((\w+)\)】", user)
    nid = m.group(1) if m else "?"
    return f"产物-{nid}"


class OrchestratorFlowTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.planner = FakeLLM(_planner_responder)
        self.worker = FakeLLM(_worker_responder)
        self.orch = XiaoOrchestrator(
            planner=self.planner, worker=self.worker, event_bus=self.bus, task_id_factory=lambda: "T1",
        )
        self.collected: dict[str, list] = {ev: [] for ev in ALL_NODE_EVENTS}
        for ev in self.collected:
            self.bus.on(ev, self.collected[ev].append)

    async def test_flow_decompose_and_execute(self) -> None:
        result = await self.orch.run("整理本周渠道周报，汇总销量、生成同比图、提炼摘要")
        self.assertEqual(result.node_count, 3)
        self.assertEqual(result.failed_count, 0)
        self.assertTrue(result.ok)
        self.assertEqual(result.nodes["n1"].output, "产物-n1")
        self.assertEqual(result.nodes["n2"].output, "产物-n2")
        self.assertEqual(result.nodes["n3"].output, "产物-n3")
        self.assertIn("汇总各代表处销量", result.output)
        self.assertIn("产物-n1", result.output)
        self.assertIn("提炼三句话摘要", result.output)

    async def test_per_node_independent_context(self) -> None:
        await self.orch.run("整理渠道周报")
        worker_calls = self.worker.calls
        self.assertEqual(len(worker_calls), 3)
        for msgs in worker_calls:
            self.assertEqual(len(msgs), 2)  # system + user，独立 context，无累积历史
            self.assertEqual(msgs[0].role, "system")
            self.assertEqual(msgs[1].role, "user")

    async def test_events_emitted_on_bus(self) -> None:
        await self.orch.run("整理渠道周报")
        self.assertEqual(len(self.collected[EVENT_NODE_PLANNED]), 3)
        self.assertEqual(len(self.collected[EVENT_NODE_STARTED]), 3)
        self.assertEqual(len(self.collected[EVENT_NODE_COMPLETED]), 3)
        self.assertEqual(len(self.collected[EVENT_NODE_FAILED]), 0)
        self.assertEqual(len(self.collected[EVENT_TASK_COMPLETED]), 1)
        data = self.collected[EVENT_NODE_DATA]
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["source_node"], "n1")
        self.assertEqual(data[0]["target_node"], "n2")
        self.assertEqual(data[0]["value"], "产物-n1")
        done = self.collected[EVENT_TASK_COMPLETED][0]
        self.assertEqual(done["task_id"], "T1")
        self.assertEqual(done["node_count"], 3)

    async def test_node_inputs_gathered_from_upstream(self) -> None:
        await self.orch.run("整理渠道周报")
        self.assertEqual(self.orch._store.get("n1"), "产物-n1")
        self.assertEqual(self.orch._store.get("n2"), "产物-n2")

    async def test_node_failure_does_not_abort_task(self) -> None:
        def flaky(messages):
            user = next((m.content for m in messages if m.role == "user"), "") or ""
            m = re.search(r"【本节点\((\w+)\)】", user)
            nid = m.group(1) if m else "?"
            if nid == "n2":
                raise RuntimeError("boom")
            return f"产物-{nid}"
        worker = FakeLLM(flaky)
        fresh_bus = EventBus()
        orch = XiaoOrchestrator(
            planner=self.planner, worker=worker, event_bus=fresh_bus, task_id_factory=lambda: "T1",
        )
        result = await orch.run("整理渠道周报")
        self.assertEqual(result.failed_count, 1)
        self.assertFalse(result.ok)
        self.assertEqual(result.nodes["n2"].status, "failed")
        self.assertIn("boom", result.nodes["n2"].error)

    async def test_planner_no_nodes_raises(self) -> None:
        def bad_planner(messages):
            return "{\"summary\": \"空\", \"nodes\": []}"
        orch = XiaoOrchestrator(
            planner=FakeLLM(bad_planner), worker=self.worker, event_bus=self.bus, task_id_factory=lambda: "T1",
        )
        with self.assertRaises(XiaoPlanError):
            await orch.run("某任务")

    async def test_empty_task_raises(self) -> None:
        with self.assertRaises(XiaoOrchestrationError):
            await self.orch.run("")


class EventRegistrationTest(unittest.TestCase):
    def test_task_events_in_event_types_whitelist(self) -> None:
        # EVENT_TYPES 白名单必须包含本模块发布的所有 task.node_* 事件
        for ev in ALL_NODE_EVENTS:
            self.assertIn(ev, EVENT_TYPES)

    def test_task_events_registered_in_registry_file(self) -> None:
        text = (ROOT / "docs" / "specs" / "EVENT_REGISTRY.md").read_text(encoding="utf-8")
        for ev in ALL_NODE_EVENTS:
            self.assertIn("`{}`".format(ev), text)

    def test_event_names_are_task_node_star(self) -> None:
        for ev in ALL_NODE_EVENTS:
            self.assertTrue(ev.startswith("task.node_") or ev == "task.completed")


class NamingRuleTest(unittest.TestCase):
    def test_no_homerail_naming_in_code(self) -> None:
        # 所有 backend 源码不得出现 homerail_ 命名（借鉴标注只允许非下划线的 HomeRail/homerail 链接）
        hits: list[str] = []
        for p in (ROOT / "backend").rglob("*.py"):
            if "homerail_" in p.read_text(encoding="utf-8"):
                hits.append(str(p))
        self.assertEqual(hits, [])

    def test_orchestrator_files_use_xiao_prefix(self) -> None:
        names = sorted(p.name for p in ORCH.glob("*.py"))
        for n in names:
            self.assertTrue(n.startswith("xiao_") or n == "__init__.py", n)

    def test_orchestrator_classes_use_xiao_prefix(self) -> None:
        # 本模块公开类必须为 Xiao* 前缀（无 Homereail/HomeRail 类名）
        from backend.orchestrator import XiaoNode, XiaoNodeKind, XiaoPlan, XiaoResult
        for cls in (XiaoNode, XiaoNodeKind, XiaoPlan, XiaoResult):
            self.assertTrue(cls.__name__.startswith("Xiao"))


class ParsePlanTest(unittest.TestCase):
    def test_parse_fenced_json(self) -> None:
        fenced = "```json\n" + PLAN_JSON + "\n```"
        plan = parse_plan(fenced, "T1")
        self.assertEqual(plan.task_id, "T1")
        self.assertEqual(len(plan.nodes), 3)
        self.assertEqual(plan.nodes[1].depends_on, ["n1"])

    def test_parse_plain_json(self) -> None:
        plan = parse_plan(PLAN_JSON, "T1")
        self.assertEqual(plan.summary, "整理本周渠道周报")
        self.assertEqual(plan.nodes[0].node_id, "n1")

    def test_parse_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_plan("", "T1")


class TopoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.planner = FakeLLM(_planner_responder)
        self.worker = FakeLLM(lambda m: "ok")
        self.orch = XiaoOrchestrator(
            planner=self.planner, worker=self.worker, event_bus=self.bus, task_id_factory=lambda: "T1",
        )

    def test_topo_order_respects_dependencies(self) -> None:
        nodes = [
            XiaoNode(node_id="n1", seq=1, summary="a", depends_on=[]),
            XiaoNode(node_id="n2", seq=2, summary="b", depends_on=["n1"]),
            XiaoNode(node_id="n3", seq=3, summary="c", depends_on=["n2"]),
        ]
        order = self.orch._topo_order(nodes)
        self.assertEqual([n.node_id for n in order], ["n1", "n2", "n3"])

    def test_topo_cycle_detected(self) -> None:
        nodes = [
            XiaoNode(node_id="n1", seq=1, summary="a", depends_on=["n2"]),
            XiaoNode(node_id="n2", seq=2, summary="b", depends_on=["n1"]),
        ]
        with self.assertRaises(XiaoOrchestrationError):
            self.orch._topo_order(nodes)


if __name__ == "__main__":
    unittest.main()