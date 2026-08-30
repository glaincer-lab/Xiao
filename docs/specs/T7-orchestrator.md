# T7-orchestrator · 任务编排层设计书

> 设计思想受 [xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）启发；
> 本模块为小二（Xiao）**自研实现**（Python），不复制 HomeRail 代码，借鉴其
> 「智慧大脑 + 高效工人」分层与 per-node 独立 context 思想。

## 0. 头部元信息

```yaml
module: T7-orchestrator     # 结构规划 §5.1 借鉴项 A（对应执行方案 T7）
version: v1.0
status: draft
depends_on: [M0-core, llm]  # 复用事件总线 + llm provider 选择
paradigm: B                 # 任务执行（提效补充，非改变伙伴定位）
owner_notes: 迁自 _audit/执行方案-2026-08-30.md T7 与 _audit/结构规划-2026-08-30.md §5.1
```

## 1. 目的与范围

- 为什么存在：`backend/agent.py` 的 `_run()` 是「单 agent、单循环、顺序工具调用」，
  对「复杂但结果可判定」的任务既贵又慢。本模块把它拆成贵模型规划 + 廉价模型执行的
  DAG 式节点流转，提质降本，保持伙伴人设的响应体验。
- 明确不做什么：不改变小二「伙伴」定位（这是提效，不是改成任务工具）；不自建 harness；
  不改 `backend/agent.py` 的核心单循环逻辑；不改 `backend/llm/` 的 provider 选择能力。

## 2. 前置依赖

- 复用模块：`backend/event_bus.py`（跨模块通信，`task.node_*` 事件）、`backend/llm/`（provider 多方案可切）。
- 现有底座：`backend/config.py`（`llm.models[]` / `orchestrator.*_scheme` 可配项）。
- 外部依赖：与 HomeRail 相同，借用 LLM API（DeepSeek 等）与本地 Ollama；均为现有 `llm/` 已支持。

## 3. 数据结构（字段级）

### XiaoNode（一个执行节点）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| node_id | str | — | 节点唯一 id（n1/n2/...） |
| seq | int | — | 规划器顺序号（稳定展示） |
| summary | str | — | 该节点子任务一句话摘要 |
| kind | XiaoNodeKind | `execute` | `plan` / `execute` |
| depends_on | list[str] | `[]` | 前置节点 id（禁止环） |
| inputs | dict | `{}` | 执行时从上游收集的数据 |
| output | str | `""` | 执行产物 |
| error | str | `""` | 失败原因 |
| status | str | `pending` | pending/running/done/failed |

### XiaoPlan / XiaoResult

- `XiaoPlan`：{task_id, summary, nodes: list[XiaoNode]}——规划器产出。
- `XiaoResult`：{task_id, node_count, failed_count, output, nodes: dict}——整任务结果。

## 4. 状态机 / 流程

```
run(task_text)
  └─ planner(贵模型) → 拆成 ≤max_nodes 个 XiaoNode（JSON）
       └─ 拓扑排序（尊重 depends_on）
            └─ 逐节点：worker(廉价模型) 用【独立 context】执行
                 ├─ 成功 → 广播 task.node_completed + task.node_data（给下游）
                 └─ 失败 → 广播 task.node_failed，不中断整任务
       └─ 合成最终回复 → 广播 task.completed → 返回 XiaoResult
```

异常路径：规划器空/无法解析 → `XiaoPlanError`；节点失败 → `XiaoNodeError`（被捕获并记进节点，不中断）；
依赖有环 → `XiaoOrchestrationError`；任务为空 → `XiaoOrchestrationError`。

## 5. 规则与话术（写死区）

- 数字默认值：`max_nodes = 8`（超过即截断并告警）；规划器提示词内嵌该上限。
- 话术：最终回复由 `_assemble` 合成——head（plan.summary 或 task）+ 每节点一行摘要:结果。
- 角色模型回退链：planner=`orchestrator.planner_scheme`→当前激活；worker=`orchestrator.worker_scheme`→`ollama` 方案→当前激活。

## 6. 模块间接口

**写入（发布到事件总线）**：见下表（已登记 `EVENT_REGISTRY.md` §一）。

| 事件名 | payload | 发布者 | 订阅者 | 备注 |
|---|---|---|---|---|
| `task.node_planned` | `{task_id,node_id,seq,kind,summary,depends_on}` | 编排层 | 可观测/下游 | 规划器为每个节点广播一次 |
| `task.node_started` | `{task_id,node_id,seq,kind,role}` | 编排层 | 可观测 | 节点开始执行 |
| `task.node_completed` | `{task_id,node_id,seq,kind,output}` | 编排层 | 下游节点/M6 | 节点完成 |
| `task.node_failed` | `{task_id,node_id,seq,kind,error}` | 编排层 | 可观测 | 节点失败 |
| `task.node_data` | `{task_id,source_node,target_node,key,value}` | 编排层 | 下游节点 | 节点间数据经总线传递 |
| `task.completed` | `{task_id,result,node_count,failed_count}` | 编排层 | M6/M1/M3 | 整任务完成 |

**读取（订阅）**：本模块目前不订阅事件（拉取式调用：由 router/上层直接 `await xiao.run(...)`）。

**被调用（暴露给路由层）**：

```python
orchestrator = XiaoOrchestrator()          # 或依赖注入 planner/worker
result = await orchestrator.run(task_text, task_id=None) -> XiaoResult
```

**事件登记强制**：本节 6 条事件已同步四处（EVENT_REGISTRY §一 + 本模块书 §6 + `backend/event_bus.py` EVENT_TYPES + `backend/orchestrator/xiao_events.py` 发布端常量）。

## 7. 验收断言

- 能拆一个复杂任务为规划/执行节点并正确流转（测试 `test_orchestrator.py`）。
- `task.node_*` 事件在 EVENT_REGISTRY + EVENT_TYPES 白名单同步（测试断言）。
- 命名无 `homerail_`，全套 `backend/orchestrator/` + `xiao_` 前缀（测试断言）。
- 单元测试全绿 + `scripts/audit_module_boundaries.py` PASS。

## 8. 开放问题

- 并发/并行执行：当前串行拓扑执行；并行（无依赖节点同时跑）留待后续。
- `orchestrator.*_scheme` 尚未接入设置页 schema；当前经 `config.get` 读取。

## 9. 变更记录

| 日期 | 版本 | 变更 | 依据 |
|---|---|---|---|
| 2026-08-30 | v1.0 | 新增编排层模块 | 执行方案 T7 / 结构规划 §5.1 |