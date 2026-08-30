# T8-audit · 可审计回放层设计书

> 设计思想受 [xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）启发；
> 本模块为小二（Xiao）**自研实现**（Python），不复制 HomeRail 代码，借鉴其
> 「append-only Activity fact plane + replay + scorecard + run 级工作区隔离」思想，
> 数据结构、命名、事件机制均为自有。

## 0. 头部元信息

```yaml
module: T8-audit          # 结构规划 §5.1 借鉴项 B（对应执行方案 T8）
version: v1.0
status: draft
depends_on: [M0-core, bridge]  # 复用事件总线（仅 M0 基础设施，不新增事件）+ bridge event_sink
paradigm: D              # 数据沉淀（审计/复盘/质量打点，只读追加、事后消费）
owner_notes: 迁自 _audit/执行方案-2026-08-30.md T8 与 _audit/结构规划-2026-08-30.md §5.1
```

## 1. 目的与范围

- 为什么存在：小二长任务走 DSH web 桥（`backend/bridge/dsh_web_bridge.py`）时，每次 run（任务轮）
  调用了哪些工具、成功/失败、助手说了什么，此前**不可回查**。本模块把桥已解析好的事件**追加式落盘**，
  事后按 run_id 重放时间线、对 tool/result 做质量打点。
- 明确不做什么：**不改 bridge 的核心解析**（`call_names` / `chunks` / `last_message` 维持原样），
  只读追加；**不改 `backend/tasks.py`** 的任务调度逻辑；**不新增跨模块事件**（订阅的是 bridge 的
  event_sink 原始事件流，非 `backend/event_bus.py` 的 `EVENT_TYPES` 白名单事件——故不触发 EVENT_REGISTRY
  四处同步）；不破坏既有记忆/画像；不依赖网络（本地落盘，脱敏后本地留存）。

## 2. 前置依赖

- 复用模块：`backend/bridge/dsh_web_bridge.py`（`_emit_raw` 上抛带 run_id 的原始事件）；
  `backend/event_bus.py`（仅沿用 M0 基建原则，本模块不发布/订阅总线事件）；
  `backend/gateway/`（R2 本地脱敏复用 `guard_outbound` 的占位混淆 + `local_only_keywords` 黑词遮蔽）。
- 现有底座：`backend/config.py`（`audit.log_dir`，默认 `logs/audit`，基于 ROOT 解析，不写死本机绝对路径）。
- 外部依赖：无新增（JSONL 落盘，标准库）；HomeRail 仅作设计思想借鉴，不含其代码/依赖。

## 3. 数据结构（字段级）

### XiaoFact（一条 append-only 事实记录，不可变、只追加）

| 字段 | 类型 | 说明 |
|---|---|---|
| seq | int | 该 run 内自增序号（追加前按已有最大 seq 续种，进程重启可续） |
| ts | float | 事件时间戳（默认 now） |
| run_id | str | run（任务轮）标识，与 `backend/tasks.py` 的 `task_id` 同为 **8 位 hex**（`uuid.uuid4().hex[:8]`），用于 run 级隔离 |
| event | str | 五类原始事件之一（见 §5） |
| payload | dict | 事件载荷（落盘前经 R2 本地脱敏） |

### 存储形态（run 级工作区隔离，与 tasks.py 的 logs/ 约定对齐）

```
<base_dir>/<run_id>/events.jsonl    ← append-only 事实日志（唯一事实来源）
<base_dir>/<run_id>/run.json        ← run 级派生缓存（可重算，非事实源）
<base_dir>/<run_id>/scorecard.json  ← 打分结果（由 scorecard 写入，非事实源）
```

- `base_dir`：来自 config（`audit.log_dir`，默认 `logs/audit`），基于 ROOT 解析，**不写死本机绝对路径**。
- 线程安全：web 桥并发多个 run 并行写入，`_seq` 计数与文件打开由 `threading.Lock` 保护。
- 生命周期：facts 追加即落盘；run.json/scorecard.json 为派生产物，可重算；`clear()` 仅测试用。

## 4. 状态机 / 流程

```
[bridge run] --_emit_raw(五类原始事件+run_id)--> event_sink
   └─ _bridge_sink(kind, payload)                ← main.py 装配层包装
        └─ auditor.handle_event(kind, payload)   ← 只读追加
             ├─ assistant/chunk → R1 限频缓冲（攒到阈值或 turn/end 才批量落盘）
             └─ 其它四类     → 先冲刷 chunk 缓冲（保到达顺序）再即时落盘
事后查询：
   auditor.replay(run_id)   → 时间线（chunk 合并为完整助手消息）
   auditor.scorecard(run_id)→ 质量打点（0-100 分 + 错误率等）
```

异常路径：携带 `run_id` 的 payload 缺失 → 丢弃该事件（无法归属）；五类之外的派生态
（`work_step` / `dsh_chunk`）→ 自动忽略；追加/脱敏异常 → 记 warning，**绝不让上游事件流中断**
（与 bridge._emit 的静默策略一致）。

## 5. 规则与话术（写死区）

### 五类原始事件（FACT_EVENT_TYPES，仅记录这五类）

| 事件 | 说明 |
|---|---|
| `tool/call` | 工具调用开始 |
| `tool/result` | 工具执行结果（含 isError） |
| `assistant/chunk` | 流式助手文本片段（高频，走 R1 缓冲） |
| `assistant/message` | 助手完整消息 |
| `turn/end` | run（任务轮）结束，含结束原因 |

派生态 `work_step` / `dsh_chunk`（供前端上屏）**不入事实平面**，自动忽略。

### R1 · 限频缓冲（高频 chunk 攒批落盘，降磁盘损耗）

- `_chunk_flush_bytes = 4096`（字节）：`assistant/chunk` 先入缓冲，累计达阈值或遇 `turn/end`
  时批量刷盘；非 chunk 事件落盘**前先冲刷缓冲**，维持事件到达顺序（防 chunk 乱序落尾）。
- 缓冲粒度不变：每 chunk 仍独立一条 fact，仅写盘时机被推迟；低频事件（tool/call/result/message）即时落盘。
- `flush()`：显式强制刷盘（turn/end 或调用方需要时）。

### R2 · 本地脱敏（落盘前防隐私明文裸奔，本地、不出网）

- 措施 1：复用 M0.2 网关 `guard_outbound` 的本地占位混淆（对 `obfuscation_mapping` 配置的实体做占位）。
- 措施 2：本地黑词遮蔽兜底——对 `compliance.local_only_keywords`（密码/密钥/身份证/自伤类等）命中片段
  连同后随值整体替换为 `[REDACTED]`（避免「密码 abc123」只遮蔽词、值明文残留）。
- 仅对文本内容字段（`text/message/input/content/output/reason/result/error/summary/prompt`）脱敏；
  `id/seq/kind` 等结构字段不动；网关/配置不可用时**保守返回遮蔽后文本，审计不阻塞**。

### scorecard 评分口径（仅评估 tool/result 质量，权重可调）

```
quality_score = clamp(100 - 错误率惩罚 - 空答惩罚 - 异常结束惩罚)
  - 错误率惩罚 = tool/result 错误率 * 60
  - 空答惩罚   = 无任何助手文本时 -25
  - 异常结束   = turn/end 非 completed 时 -10
```
正常结束 `turn_end_ok` 命中集合：`completed / done / ok / finished`。

## 6. 模块间接口

### 订阅：bridge event_sink（五类原始事件，只读追加）

| 通道 | payload | 发布者 | 订阅者 | 备注 |
|---|---|---|---|---|
| bridge event_sink | `{run_id, ...}`（五类原始事件） | bridge（_emit_raw） | `XiaoAuditor.handle_event` | 仅追加式上抛，不改桥解析 |

> **事件登记说明**：本模块订阅的是 bridge 的 **event_sink 原始事件流**（非跨模块事件总线），
> `run_id` 由 bridge 在 `_emit_raw` 时注入。**因此不新增 `backend/event_bus.py` 的 `EVENT_TYPES`
> 白名单事件，不触达 EVENT_REGISTRY §三 的四处同步**——这是与 T7（编排层，新增 `task.node_*` 事件）最大的区别。

### 被调用（暴露给装配层 / 上层）

```python
from backend.audit import build_auditor
auditor = build_auditor()                 # main.py 装配层 startup 持有
auditor.handle_event(kind, payload)       # bridge event_sink 注入（五类原始事件）
timeline = auditor.replay(run_id)         # 重放时间线（list[dict]）
text     = auditor.render(run_id)         # 人类可读渲染
card     = auditor.scorecard(run_id)      # 质量打点（dict，可 JSON 序列化）
runs     = auditor.runs()                 # 已记录 run_id 清单
auditor.flush()                           # 显式刷 RAM 缓冲（turn/end 自动触发）
auditor.close()
```

### 模块文件

| 文件 | 职责 |
|---|---|
| `xiao_audit.py` | `XiaoAuditor`：bridge event_sink 订阅方；R1 限频缓冲 + R2 本地脱敏；replay/scorecard 便捷入口 |
| `xiao_fact_plane.py` | `XiaoFactPlane`：run 级 append-only 事实平面（JSONL，逐 run 独立目录，线程安全） |
| `xiao_replay.py` | `XiaoReplay`：按 run_id 重放生成时间线（chunk 合并）+ 人类可读渲染 |
| `xiao_scorecard.py` | `XiaoScorecard`：对 run 的 tool/result 做质量打点（0-100 分） |

## 7. 验收断言

- 能按 run_id 重放五类事件成有序时间线，流式 chunk 合并为完整助手消息（`tests/test_xiao_audit.py` 20 项全绿）。
- R1：`assistant/chunk` 限频缓冲在 turn/end 或达阈值时批量落盘（断言 `event_path` 写盘时机）。
- R2：敏感字段落盘前脱敏，`local_only_keywords` 命中片段与后随值替换为 `[REDACTED]`（断言脱敏）。
- run 级隔离：不同 run_id 写入不同目录；append-only（追加不改写已有行；`facts()` 按 seq 升序）。
- 质量打分：错误率/空答/异常结束计入 0-100 分（`turn_end_ok` 判定）。
- 组件命名全套 `backend/audit/` + `xiao_` 前缀，无 `homerail_`（测试断言）。
- 单测全绿 + `scripts/audit_module_boundaries.py` PASS（模块边界：audit 不 import bridge/tasks 核心）。

## 8. 开放问题

- 事实平面当前为 JSONL 追加、逐条 open；run 量大后是否归档/压缩待定。
- scorecard 权重（错误率×60 / 空答-25 / 异常-10）尚未配置化；当前写死，可调项待后续暴露。
- `run.json` 派生缓存尚未使用（当前仅 events.jsonl + scorecard.json 生效）；保留为 run 级元数据扩展点。

## 9. 变更记录

| 日期 | 版本 | 变更 | 依据 |
|---|---|---|---|
| 2026-08-30 | v1.0 | 新增可审计回放层模块 | 执行方案 T8 / 结构规划 §5.1 |
