# 小二（Xiao）· M1 向量记忆与存储治理 · 落地进度

> **用途**：M6-progress §六「待实现清单」前 3 项（向量库阶段一 / 存储治理落地 / routes 脱敏）+ 后续收口（调度接线 / 存储满弹窗 / 巩固增量 / 双轨呈现）的落地基线。
> **日期**：2026-08-31 ｜ **版本**：v2.0 ｜ **前置**：M6 完成（见 docs/progress/M6-progress.md）
> **规格依据**：docs/specs/M1-vector-memory.md（v4.4.0，全部决策已定稿）

---

## 一、完成概览

按「判断(Pro) / 执行(Flash)」分工，全部落地（模块能力 + 单元测试全绿）：

| 阶段 | 内容 | 状态 |
|---|---|---|
| 阶段1 | 向量库阶段一：encode + 向量存储层 + 四因子检索 + 热层索引 + 降级兜底 | ✅ 完成（Pro） |
| 阶段2 | 存储治理：三层时间窗口失效 + 容量上限 + P0 保护 + 存储满阈值 | ✅ 完成（Pro） |
| 阶段2c | 设置引导：settings「存储」组 + config memory 段 | ✅ 完成（Flash + Pro 修正） |
| 阶段3 | routes.jsonl 脱敏降级 + 容量上限 | ✅ 完成（Flash） |
| 收口1 | Sleeptime 调度接线（检索 + 巩固 + 治理 + 定时兜底） | ✅ 完成（Pro） |
| 收口2 | 存储满弹窗（80%/95% 前端弹窗 + 清理反馈） | ✅ 完成（Pro） |
| 收口3 | 巩固增量机制（last_consolidated_ts） | ✅ 完成（Pro） |
| 收口4 | 成长双轨回顾面板（/api/recall + 前端三栏） | ✅ 完成（Flash） |

## 二、文件清单

### 新增（6 个代码 + 6 个测试）
| 文件 | 说明 |
|---|---|
| backend/memv1/vector_store.py | 向量存储层：sqlite-vec 主选 + numpy 兜底（upsert/query/invalidate/delete/rebuild + 工厂 + make_retriever） |
| backend/memv1/governance.py | 存储治理：失效标记 + 容量清理 + P0 保护 + 80%/95% 阈值 + 预算解析 |
| backend/memv1/indexer.py | 热层索引：画像 + 共同记忆 + 成长三轨 → 向量投影（派生索引，可重建） |
| backend/memv1/maintenance.py | 记忆后台管家：索引/治理/巩固节流 + 定时兜底 + 阈值事件 + 清理动作 |
| tests/test_vector_store.py / test_memv1_vector_recall.py / test_governance.py / test_hot_index.py / test_maintenance.py / test_router.py | 单测共 44 项 |

### 修改（8 个）
| 文件 | 说明 |
|---|---|
| backend/gateway/semantic_filter.py | 新增 encode(text)->list[float]，__all__ 增 encode/SemanticUnavailable |
| backend/memv1/retrieval.py | 四因子排序（rank_entries）+ 向量召回注入 + build_injection 改造 + 降级兜底 |
| backend/memv1/consolidate.py | 巩固增量：last_consolidated_ts 只提炼新增日志 |
| backend/memv4.py | DataTrack 新增 prune_before（短期全量层清理） |
| backend/agent.py | 检索侧 set_vector_retriever + 对话结束 run_after_turn |
| backend/main.py | start_sweeper + /api/recall + storage_action/清理反馈 |
| backend/router.py | routes 脱敏 + 容量轮转（Flash） |
| backend/settings_schema.py + config.yaml | 「存储」组 + memory 段（Flash + Pro 修正） |
| frontend/src/App.tsx + styles.css | 存储满弹窗 + 成长双轨回顾面板（Flash） |

## 三、测试结果

- 全量 **942 项**（= 900 基线 + 42 新增），新增全绿。
- 10 项失败为既有沙箱环境问题：全部在 tests/test_memory.py（v3 MemoryStore 用 tempfile 落系统 Temp，DSH 沙箱拒写），与本次改动无关（M6-progress §三已记录同因）。

## 四、调度与接线（Sleeptime 模式）

调度形态（老板定稿）：**聊完天触发 + 空闲消化，定时只做兜底**。

- 检索侧：agent.py 接 set_vector_retriever(make_retriever(get_vector_store()))，向量库空/不可用自动降级全量注入。
- 对话结束触发：agent.py handle finally 接 run_after_turn()，daemon 异步跑「索引 → 治理 → 巩固（15 分钟节流）」。
- 定时兜底：main.py startup 接 start_sweeper()，6 小时一次治理，幂等。
- 存储满弹窗：sweep_now 产出 ok/warn/critical，80%/95% 阈值变化时推送 storage_threshold 事件，前端三选一（清理/提升空间/暂不处理），清理走 enforce_capacity（P0 永不失效），结果反馈「已清理 N 条」。

## 五、待续清单

| # | 问题 | 说明 |
|---|---|---|
| 1 | sqlite-vec 本机验证 | 本机沙箱拦 pip，SqliteVecStore 按官方 API 编写但未运行验证；numpy 兜底已完整测通。打包时需在装有 sqlite-vec 的环境验证 + 核验 LICENSE |

## 六、提交记录（已提交）

| commit | 内容 |
|---|---|
| d92c7dd | feat(memv1): 向量存储层 sqlite-vec+numpy 兜底 |
| d14b13e | feat(gateway): 暴露 semantic_filter.encode |
| 490a85d | feat(memv1): 四因子向量召回 + 降级兜底 |
| 40e5dbb | feat(memv1): 存储治理 + 热层索引 + DataTrack 清理 |
| 9f368dc | feat(router): routes 脱敏降级 + 容量轮转 |
| 067bf5b | feat(settings): 存储组 + memory 配置段 |
| afe8473 | feat(memv1): 记忆后台管家 Sleeptime 调度 + 接线 |
| b288f5e | feat(memv1): 存储满弹窗后端 阈值事件+清理动作 |
| 407013a | feat(web): 存储满弹窗前端 UI |
| a14da00 | feat(memv1): 存储清理结果反馈 |
| 0c3b0e6 | feat(memv1): 巩固增量机制 last_consolidated_ts |
| efee729 | feat(web): 成长双轨回顾面板 /api/recall |
