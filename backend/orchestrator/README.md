# backend/orchestrator · 任务编排层（T7）

> 设计思想受 [xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）启发。
> 本模块为小二（Xiao）**自研实现**（Python），不复制 HomeRail 代码；借鉴其「智慧大脑 + 高效工人」
> 分层与 per-node 独立 context 思想，数据结构、命名、事件机制均为自有。

## 定位

介于 router 与 DSH 之间，把「复杂但结果可判定」的任务拆成「规划/执行」节点：

- **规划器（planner）**：贵模型（默认 DeepSeek 等），把任务拆成若干自包含子节点；
- **执行器（worker）**：廉价模型（默认 Ollama 本地 / 定向便宜模型），逐节点执行；
- **per-node 独立 context**：每个执行节点构建全新消息序列（系统 + 任务 + 节点摘要 + 上游产物），不共享/不累积上游会话历史；
- **节点间数据**：统一走 `backend/event_bus.py` 的 `task.node_*` 事件，不引入新通信机制。

> 边界：这是对现有 Agent 单循环（`backend/agent.py` 的 `_run`）的**提效补充**，
> 不改变小二「伙伴」定位，也不自建 harness；不使用 agent 核心循环。

## 使用

```bash
.venv\Scripts\python.exe -c "import asyncio; from backend.orchestrator import XiaoOrchestrator; asyncio.run(XiaoOrchestrator().run('把本周渠道周报整理成三句话摘要'))"
```

角色模型默认解析（可配 `orchestrator.planner_scheme` / `orchestrator.worker_scheme` 指向 `llm.models[]` 的方案 id）：

| 角色 | 默认来源 | 说明 |
|---|---|---|
| planner | `orchestrator.planner_scheme` 或当前激活方案 | 贵模型（DeepSeek 等） |
| worker | `orchestrator.worker_scheme` 或名为 `ollama` 的方案 或当前激活方案 | 廉价模型 |

## 模块文件

| 文件 | 职责 |
|---|---|
| `xiao_core.py` | 引擎：规划 → 逐节点执行 → 合成；事件收发、拓扑排序 |
| `xiao_models.py` | 数据模型：XiaoNode / XiaoPlan / XiaoResult / XiaoNodeKind |
| `xiao_events.py` | 事件名常量（`task.node_*`）与发布端全集 |
| `xiao_prompt.py` | 规划器/执行器提示词构建与 plan JSON 解析 |
| `xiao_errors.py` | 编排层异常 |

## 事件（已在 EVENT_REGISTRY.md 登记并同步 EVENT_TYPES 白名单）

`task.node_planned` `task.node_started` `task.node_completed` `task.node_failed` `task.node_data` `task.completed`

## 设计书

详见 `docs/specs/T7-orchestrator.md`。