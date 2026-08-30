"""编排层提示词构建（规划器 + 执行器）。

设计思想受 [xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）启发，
自研实现：贵模型规划 / 廉价模型执行分角色系统提示，per-node 独立 context。

仅标准库。
"""

from __future__ import annotations

import json

from backend.llm.base import ChatMessage

from backend.orchestrator.xiao_models import XiaoNode, XiaoPlan

# 规划器系统提示：要求输出固定 JSON（数量有限、单层依赖、避免环）。
PLANNER_SYSTEM = (
    "你是小二（Xiao）的「智慧大脑」规划器。你负责把一个复杂但结果可判定的任务"
    "拆解成若干顺序/并行的小执行节点，交给「高效工人」分步完成。\n"
    "输出要求：\n"
    "1. 只能输出一个 JSON 对象，不要输出任何解释文字或 Markdown 代码块围栏。\n"
    "2. 最多 {{max_nodes}} 个节点；每个节点尽量自包含、结果可判定。\n"
    "3. 每个节点有：id（n1/n2/...）、summary（该节点一句话子任务）、depends_on（前置节点 id 数组）。\n"
    "4. 依赖只能指向更早的节点（禁止环）。\n"
    'JSON 结构：{"summary": "...", "nodes": [{"id": "n1", "summary": "...", "depends_on": []}]}')

# 执行器系统提示：per-node 独立 context，仅拿到任务 + 本节点 + 上游数据。
WORKER_SYSTEM = (
    "你是小二（Xiao）的「高效工人」。你只执行被分配的那一个节点，不做全局规划。\n"
    "你会拿到：整任务描述、你的节点摘要、上游节点产物。\n"
    "请只输出本节点的执行结果（简洁、可作下游输入），不要复述任务、不要输出 JSON 包装。"
)


def build_planner_messages(task_text: str, max_nodes: int = 6) -> list[ChatMessage]:
    """构建规划器消息（贵模型），要求把任务拆成 ≤max_nodes 个节点。"""
    system = PLANNER_SYSTEM.replace("{{max_nodes}}", str(max_nodes))
    user = (
        "请把下面的任务拆解为执行节点：\n\n"
        + "【任务】" + task_text + "\n\n"
        + f"只输出 JSON 对象，节点数不超过 {max_nodes}。"
    )
    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=user),
    ]


def build_worker_messages(
    task_text: str,
    plan_summary: str,
    node: XiaoNode,
    inputs: dict,
) -> list[ChatMessage]:
    """构建单个执行节点消息（廉价模型），per-node 独立 context（全新序列）。"""
    parts: list[str] = [f"【整任务】{task_text}"]
    if plan_summary:
        parts.append(f"【任务概括】{plan_summary}")
    parts.append(f"【本节点({node.node_id})】{node.summary}")
    if inputs:
        parts.append("【上游产物】" + json.dumps(inputs, ensure_ascii=False))
    else:
        parts.append("【上游产物】无")
    return [
        ChatMessage(role="system", content=WORKER_SYSTEM),
        ChatMessage(role="user", content="\n\n".join(parts)),
    ]


def parse_plan(content: str, task_id: str) -> XiaoPlan:
    """解析规划器输出为 XiaoPlan；容错处理 markdown 围栏 / 前后多余文字。

    解析失败抛 ValueError（由调用方转为 XiaoPlanError）。
    """
    text = (content or "").strip()
    if not text:
        raise ValueError("规划器返回空内容")
    obj = _coerce_json(text)

    if not isinstance(obj, dict):
        raise ValueError("规划器输出应为 JSON 对象")

    summary = str(obj.get("summary", "") or "")
    raw_nodes = obj.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError("规划器未产出任何执行节点")

    nodes: list[XiaoNode] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            continue
        nid = str(raw.get("id", f"n{i + 1}"))
        if not nid or nid in seen:
            raise ValueError(f"节点 id 缺失或重复: {nid!r}")
        seen.add(nid)
        deps = raw.get("depends_on", []) or []
        if isinstance(deps, str):
            deps = [deps]
        nodes.append(
            XiaoNode(
                node_id=nid,
                seq=i + 1,
                summary=str(raw.get("summary", "") or "").strip(),
                depends_on=[str(d) for d in deps],
            )
        )

    return XiaoPlan(task_id=task_id, summary=summary, nodes=nodes)


def _coerce_json(text: str) -> object:
    """从模型输出中抽出第一个 JSON 对象并解析（容忍围栏与前后文字）。"""
    # 先整体尝试
    if text.startswith(("{", "[")):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # 找到最外层 {...} 块（逐字符扫描，处理字符串内的引号与转义）
    start = text.find("{")
    if start >= 0:
        depth = 0
        in_str = False
        esc = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:idx + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError("无法从规划器输出中解析出 JSON")