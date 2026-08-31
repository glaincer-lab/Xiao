# 小二（Xiao）· M1 向量记忆与存储治理 · 落地进度

> **用途**：M6-progress §六「待实现清单」前 3 项（向量库阶段一 / 存储治理落地 / routes 脱敏）的落地基线。
> **日期**：2026-08-31 ｜ **版本**：v1.0 ｜ **前置**：M6 完成（900 测试绿，见 docs/progress/M6-progress.md）
> **规格依据**：docs/specs/M1-vector-memory.md（v4.4.0，全部决策已定稿）

---

## 一、完成概览

按「判断(Pro) / 执行(Flash)」分工落地，四个阶段全部完成（模块能力 + 单元测试全绿）：

| 阶段 | 内容 | 状态 |
|---|---|---|
| 阶段1 | 向量库阶段一：encode + 向量存储层 + 四因子检索 + 热层索引 + 降级兜底 | ✅ 完成（Pro） |
| 阶段2 | 存储治理：三层时间窗口失效 + 容量上限 + P0 保护 + 存储满阈值 | ✅ 完成（Pro） |
| 阶段2c | 设置引导：settings「存储」组 + config memory 段 | ✅ 完成（Flash + Pro 修正） |
| 阶段3 | routes.jsonl 脱敏降级 + 容量上限 | ✅ 完成（Flash） |

## 二、文件清单

### 新增（4 个代码 + 4 个测试）
| 文件 | 说明 |
|---|---|
| backend/memv1/vector_store.py | 向量存储层：sqlite-vec 主选 + numpy 兜底（upsert/query/invalidate/delete/rebuild + 工厂 + make_retriever） |
| backend/memv1/governance.py | 存储治理：失效标记 + 容量清理 + P0 保护 + 80%/95% 阈值 + 预算解析 |
| backend/memv1/indexer.py | 热层索引：画像 + 共同记忆 + 成长三轨 → 向量投影（派生索引，可重建） |
| tests/test_vector_store.py / test_memv1_vector_recall.py / test_governance.py / test_hot_index.py | 单测共 29 项 |

### 修改（6 个）
| 文件 | 说明 |
|---|---|
| backend/gateway/semantic_filter.py | 新增 encode(text)->list[float]（暴露已有 _embed），__all__ 增 encode/SemanticUnavailable |
| backend/memv1/retrieval.py | 新增四因子排序（rank_entries）+ 向量召回注入（set_vector_retriever）+ build_injection 改造 + 降级兜底 |
| backend/memv4.py | DataTrack 新增 prune_before(ts_threshold)（短期全量层清理） |
| backend/router.py | 脱敏 _sanitize + 容量轮转（字节/行数上限 + 按天归档 + 保留 N 天），Flash 实现 |
| backend/settings_schema.py | 新增「存储」组 + 5 字段；select 值用 str 与前端 FieldOption 对齐 |
| config.yaml | 新增 memory 段（默认 300MB + 四因子等权 + 窗口 730/3650 + Top-K=8） |

## 三、测试结果

- 全量 936 项（= 900 基线 + 36 新增），36 项新增全绿。
- 10 项失败为既有沙箱环境问题：全部在 tests/test_memory.py（v3 MemoryStore 用 tempfile 落系统 Temp，DSH 沙箱拒写），与本次改动无关（M6-progress §三已记录同因）。本次未触碰 memory.py / test_memory.py。

## 四、接线（已完成，Sleeptime 调度）

调度形态（老板定稿）：**聊完天触发 + 空闲消化，定时只做兜底**。新增 backend/memv1/maintenance.py（记忆后台管家）：

1. 检索侧：agent.py _setup_m1_provider 已接 set_vector_retriever(make_retriever(get_vector_store()))——向量库空/不可用自动降级全量注入。
2. 对话结束触发：agent.py handle finally 已接 run_after_turn()——daemon 线程异步跑「索引 → 治理 → 巩固（15 分钟节流）」，不阻塞对话。
3. 定时兜底：main.py startup 已接 start_sweeper()——daemon 长间隔（6 小时）做治理，幂等。
4. 存储满弹窗：后端 sweep_now 已产出 ok/warn/critical 阈值状态；前端弹窗 UI 待续（见 §五）。

## 五、待续清单

| # | 问题 | 说明 |
|---|---|---|
| 1 | 前端存储满弹窗 UI | 后端 sweep_now 已产出 ok/warn/critical，但前端「80% 提醒 / 95% 强提示 → 用户选清理/扩容/暂不处理」的弹窗尚未接 |
| 2 | sqlite-vec 本机验证 | 本机沙箱拦 pip，SqliteVecStore 按官方 API 编写但未运行验证；numpy 兜底已完整测通。打包时需在装有 sqlite-vec 的环境验证 + 核验 LICENSE |
| 3 | 巩固增量机制 | consolidate 每次提炼全部 session_logs（非增量），15 分钟节流已缓解频率；长期需增量标记优化 |

## 六、提交建议（Conventional Commits，未提交）

| 顺序 | commit | 文件 |
|---|---|---|
| 1 | feat(memv1): 向量存储层 sqlite-vec+numpy 兜底 | backend/memv1/vector_store.py + tests/test_vector_store.py |
| 2 | feat(gateway): 暴露 semantic_filter.encode | backend/gateway/semantic_filter.py |
| 3 | feat(memv1): 四因子向量召回 + 降级兜底 | backend/memv1/retrieval.py + tests/test_memv1_vector_recall.py |
| 4 | feat(memv1): 存储治理 + 热层索引 + DataTrack 清理 | governance.py + indexer.py + backend/memv4.py + 测试 |
| 5 | feat(router): routes 脱敏降级 + 容量轮转 | backend/router.py + tests/test_router.py |
| 6 | feat(settings): 存储组 + memory 配置段 | settings_schema.py + config.yaml |