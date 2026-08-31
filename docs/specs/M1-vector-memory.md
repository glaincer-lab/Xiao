# M1-vector-memory · 向量记忆与存储治理 · 模块设计书

[yaml]
module: M1-vector-memory
version: v4.2.0
status: draft
depends_on: [M0-core, M1-memory, M6-growth]
paradigm: D
owner_notes: 增强 M1 检索注入（retrieval.py）；新增存储容量治理（DataTrack/审计/路由）
[/yaml]

## 1. 目的与范围

为什么存在：M1 检索注入现状是「全量收集 → 规则过滤（sandbox/expired/任务态）→ 全量注入」（retrieval.py 的 build_injection → render_injection）。记忆随使用增长后，注入 token 膨胀、且无关记忆混入污染上下文。改为「向量语义召回 Top-K」，既省 token 又更准——这是「伙伴记得住」的前提。

存储治理：数据轨/审计/路由为 append-only 无限增长，需「容量上限 + 最低一次单元」自动清理（老板定稿口径）。

明确不做：
- 知识图谱（Graphiti/Zep 那类，过重）
- 云端向量库（本地优先，数据不上云）
- LLM 自动管理分页（MemGPT 那类，过重；首版用固定规则）
- 心理标签（人格宪法红线）

## 2. 前置依赖

| 依赖 | 现状 | 用途 |
|---|---|---|
| bge-small-zh-v1.5 | 已有（models/gateway-semantic/，semantic_filter.py 用 onnxruntime 加载） | 文本→向量 embedding（512 维） |
| sqlite-vec | 新增（pip install sqlite-vec，SQLite 单文件扩展，零服务器） | 向量存储 + 相似度检索 |
| M1 retrieval.py | 已有（build_injection / render_injection / set_entry_provider） | 注入入口，改造为向量召回 |
| M1 巩固层 consolidate.py | 已有（会话→摘要→画像条目） | 摘要化（压缩即提炼） |
| GrowthStore / DataTrack | 已有 | 共同记忆/成长三轨（热层素材）+ 原始流水（冷层） |

注意：semantic_filter.py 现有接口只「判断语义相关」（judge_context），未暴露「文本→向量」；需从中抽取/新增 encode(text)->list[float] 接口，复用已有 bge 加载逻辑。sqlite-vec 许可证落地前核实（调研为 stacklok 项目，预期 MIT）。

## 3. 数据结构（字段级）

### 3.1 向量条目（热层，进向量库）

sqlite-vec 表 memories 字段：
- id：唯一 id（hex12）
- text：摘要/记忆内容（检索返回的正文）
- embedding：f32[512]（bge-small-zh-v1.5 向量）
- meta：kind（semantic/episodic/milestone）、scope、effective_at、source、status、confidence、importance（检索加权用）
- ts：时间戳（近因加权）

### 3.2 冷热分层

| 层 | 内容 | 存储 | 生命周期 |
|---|---|---|---|
| 热层 | 画像条目（五要素）+ 共同记忆 shared_memories + 成长三轨 | 向量库（摘要+向量） | 常驻，Top-K 召回 |
| 冷层 | 原始流水（memv4 数据轨三层）+ 审计 run + 路由 | 原文件（JSON/JSONL） | 容量上限自动清理 |

### 3.3 与 M1 五要素 schema 的关系

向量条目是 M1 画像条目 + M6 共同记忆的「检索投影」，不是新的真源：真源仍是 M1 画像存储 + GrowthStore。向量库是派生索引，可由真源重建（导出/迁移时可选不打包向量，靠真源重建）。

## 4. 状态机 / 流程

### 4.1 写入（对话后异步，复用巩固层）
新记忆条目（画像/共同记忆/里程碑）→ 摘要化（复用 consolidate，已摘要则跳过）→ encode(摘要) → upsert 向量库（id 幂等）

### 4.2 检索（注入时）
user_request → encode(query) → sqlite-vec 相似度 Top-K（K=8 可调）→ 加权排序（relevance+recency+importance）→ 过滤 sandbox/expired → render_injection

### 4.3 容量清理（后台，触发式）
检测总容量 > 上限（记忆流水 50MB / 审计 30MB / 路由 10MB）→ 按「单元」从最旧开始整体删（记忆流水/路由单元=天；审计单元=run）→ 直到总容量 ≤ 上限 或 只剩最近 1 个单元 → 单单元超上限整体保留（不切碎），接受临时超限

### 4.4 异常路径
| 异常 | 处理 |
|---|---|
| embedding 模型缺失/加载失败 | 降级全量注入（回退现状行为），记结构化日志 |
| sqlite-vec 不可用 | 降级 numpy 暴力检索（零依赖兜底） |
| 向量库损坏 | 重建空库，靠真源重新向量化 |
| 清理中并发写 | 加锁，清理与写入互斥 |

## 5. 规则与话术（写死区）

| 规则 | 默认值 | 可调 |
|---|---|---|
| 记忆流水容量上限 | 50 MB（单元=天） | 是 |
| 审计容量上限 | 30 MB（单元=run） | 是 |
| 路由容量上限 | 10 MB（单元=天） | 是 |
| 任务/待授权 | 各 200 条 | 是 |
| 向量召回 Top-K | 8 | 是 |
| embedding 维度 | 512（bge-small-zh-v1.5） | 否 |
| 最低一次 | 至少保留最近 1 个完整单元；单单元超限整体保留 | 否 |

铁则：共同记忆 + 成长三轨（P0）不进任何容量自动清理，仅用户手动删（forget）。

设置引导：settings_schema 加「存储」组，首次向导给「存储预算档位」——轻量 100MB / 标准 500MB / 充裕 2GB / 自定义，内部按比例分配（记忆流水 40% / 审计 40% / 路由 20%），高级模式展开单项调整。

## 6. 模块间接口（事件契约）

本模块不新增事件、不改 EVENT_TYPES 白名单：向量检索是 M1 retrieval.py 内部增强（build_injection 内部改向量召回），容量清理是 DataTrack/审计/路由各自的内部后台任务，均不产生跨模块事件。跨模块交互复用现有事件（memory.profile_updated 等）。

## 7. 验收断言

- 召回相关性：给定 query，Top-K 含语义相关记忆（非仅关键词命中）。
- 降级兜底：embedding 不可用 → 自动降级全量注入，功能不中断。
- 容量清理：超限 → 删到回上限；最低保留最近 1 个单元；不切碎单元（单 run/单天整体删）。
- 共同记忆不可自动删：容量清理不触碰 growth.json 三轨（断言清理前后三轨计数不变）。
- 可重建：删向量库后，靠真源（画像 + GrowthStore）能重建索引。
- 迁移：向量库随 9 类资产导出，或标记「可重建」不打包（择一，见开放问题 2）。
- 链接 EVAL.md 场景三（任务态数据轨直通）不因向量化而破坏。

## 8. 开放问题

1. embedding 模型缺失时，是「全量注入降级」还是「无记忆降级」？（倾向全量注入，保底不丢功能）
2. 向量库迁移：随 9 类资产打包（体积大）vs 标记可重建（新机重新向量化，慢）？
3. 检索加权 recency/importance/relevance 三因子权重未定（首版可等权，观察后调）。
4. sqlite-vec 的 Windows 打包与许可证需落地前核实（备选 numpy 暴力零依赖）。
5. 向量化时机：对话后同步 vs 后台空闲（倾向后台，复用 consolidate 的异步模式）。

## 9. 变更记录

| 日期 | 版本 | 变更 | 依据 |
|---|---|---|---|
| 2026-08-31 | v4.2.0 | 自 ROADMAP §4.M1 + M6-growth 迁出成书；并入向量检索 + 容量治理 + 最低一次单元 | 老板拍板 + 业界调研（MemGPT/memobase/Mem0/sqlite-vec） |