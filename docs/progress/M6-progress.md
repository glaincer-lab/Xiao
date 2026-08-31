# 小二（Xiao）· M6 开发与记忆架构决策 · 阶段性总结

> **用途**：本阶段（M6 六包开发 → 验收 → 存储治理决策 → 迁移补齐 → 向量库立项）的**可接续基线**。下一个对话接续实现「向量库 + 存储治理」时，先读本文 + 设计书 `docs/specs/M1-vector-memory.md`。
> **日期**：2026-08-31 ｜ **版本**：v1.0 ｜ **前置**：M0-M5 已完成（见 `docs/progress/M0-M5-progress.md`）。

---

## 一、本阶段概览

老板拍板「先功能落地 M6，再技术收口」，按 **PRO（判断）/ FLASH（执行）** 分工完成：
- **M6 成长主线** 6 个后端包全量实现 + 验收；
- **存储治理** 决策（被遗忘权可删 + 容量上限 + 最低一次单元）；
- **迁移缺口** 补齐（世界观/哀伤/入册状态/纪念锚点）；
- **向量库** 立项（阶段一：sqlite-vec + 复用 bge embedding）。

---

## 二、已完成产出

### 2.1 M6 六包（后端全绿）

| 任务包 | 文件 | 说明 |
|---|---|---|
| 成长记录数据层 | `backend/m6/growth.py` | 双轨持久层 + root/reload 公开接口 |
| 双源入册制 | `backend/m6/canonize.py` | 任务完成+S6 正向→册封，Aging Policy |
| 微小请求三类 | `backend/m6/micro_request.py` | feedback/preference/human_experience，月级冷却 |
| 记忆导出迁移 | `backend/m6/export.py` | 九类资产打包/导入 + 选择性删除 |
| 纪念锚点见证 | `backend/m6/memorial.py` | 复用 M1.5 人物卡，月度可关闭 |
| 回顾推送 | `backend/m6/recall.py` | 双轨分栏 + 共同回忆「咱们」叙事 |

### 2.2 M6 后端验收（PRO/FLASH）

6 个 Flash 审查子代理并行逐包读码对照（设计书 §4/§7 + 铁规 + 冻结接口），Pro 汇总裁决。结论：**通过**。修复 1 个跨包契约缺陷（GrowthStore 缺公开 root/reload 致 3 处触碰私有成员）。

### 2.3 迁移缺口补齐

`export.py` 从 5 类扩到 9 类：新增「世界观（逐文件保真）/ 哀伤标签 / 入册状态 / 纪念锚点」。测试 15/15。

---

## 三、测试基线（重大变化）

| 项 | 之前 | 现在 |
|---|---|---|
| 全量测试 | 822（failures=2, errors=9） | **900（OK，0 失败）** |
| 失败项 | 11 项 | **0 项** |
| 模块边界纪律 | — | **PASS**（8 项待复核，均合理装配） |

**失败清零归因**：① test_dsh_web_bridge 1 项 = 审计整改引入 `_emit_raw` 后 event_sink 变混合流、测试未同步（修复=过滤派生态断言，非实现 bug）；② test_memory 9 项 = 沙箱拒写系统 Temp（本次会话沙箱放宽为 danger-full-access 后自动通过）。

---

## 四、关键决策记录（本阶段核心，新对话必须遵守）

| # | 决策 | 结论 |
|---|---|---|
| 1 | 被遗忘权边界 | **用户要求可删一切（含原始层）**；数据轨「零丢失」承诺作废（口径已修正） |
| 2 | 存储治理口径 | **不按期限、按容量**；清理按「单元」整体删、不切碎；**最低保留最近一个完整单元** |
| 3 | 容量默认值 | 记忆流水 50MB / 审计 30MB（单元=run）/ 路由 10MB（单元=天）/ 任务·待授权各 200 条 |
| 4 | 共同记忆 | **P0 永不自动删**，仅用户手动删——「不是自己是谁，而是我们经历了什么」 |
| 5 | routes.jsonl | **脱敏降级保留**（黑词遮蔽 + 容量上限），明文全量仅调试模式 |
| 6 | 向量库 | **立项阶段一**：复用 bge-small-zh-v1.5 + sqlite-vec（或 numpy 暴力，零依赖） |
| 7 | 迁移 | 数据包=9 类资产 + 配置模板；**密钥绝不进包**，新机走向导重填 |
| 8 | 微小请求冷却槽 | 维持现状（选 A，不改设计书 §3 单槽结构） |

---

## 五、提交清单（11 笔，Conventional Commits）

| 提交 | 类型 | 内容 |
|---|---|---|
| `cd4f3c7` | test(bridge) | e2e 流断言适配混合流 |
| `f9d491b` | feat(m6) | 成长记录双轨数据层 GrowthStore |
| `bb4eb94` | feat(m6) | 双源入册制 Canonizer |
| `04f1e3c` | feat(m6) | 微小请求三类 MicroRequester |
| `93b518f` | feat(m6) | 记忆全量导出迁移 MemoryExporter |
| `6acf2ca` | feat(m6) | 纪念锚点见证 MemorialWitness |
| `e2b224e` | feat(m6) | 回顾推送 RecallComposer 并统一包导出 |
| `33141af` | docs | M6 完成收口更新 ROADMAP 实现进度 |
| `607b3db` | refactor(m6) | 暴露 GrowthStore.root/reload 消除私有成员触碰 |
| `709f3bf` | docs | 数据轨从零丢失修正为可删除（被遗忘权） |
| `af0f01f` | feat(m6) | 补齐迁移缺口（世界观/哀伤/入册状态/纪念锚点） |

---

## 六、待实现清单（供新对话接续）

1. **向量库阶段一**（见设计书 `docs/specs/M1-vector-memory.md`）——语义检索替代全量注入 + 冷热分层。
2. **存储治理落地**——容量上限 + 最低一次单元（默认值见 §四-3）+ 设置引导（存储预算档位：轻量 100MB / 标准 500MB / 充裕 2GB）。
3. **routes.jsonl 脱敏降级**——复用 `xiao_audit.py` 的 `_sanitize_payload` 思路，脱敏 + 容量上限。
4. **遗留（不阻塞，可选）**：recall.now_fn 删除（已确认无设计意义）、前端双轨呈现 F1、M4 真实 VLM/摄像头、M5 HA 真实 REST/WebSocket、Piper GPL 边界。

---

## 七、关键文件索引

- 进度基线：`docs/progress/M0-M5-progress.md`（M0-M5）、本文（M6）
- 设计书：`docs/specs/M6-growth.md`、`docs/specs/M1-vector-memory.md`（新，待实现）
- 拆解计划 + 完成记录 + 验收结论：`_audit/拆解计划-M6-2026-08-31.md`（gitignored 本地留档）
- M6 代码：`backend/m6/`（growth/canonize/micro_request/export/memorial/recall）
- 测试：`tests/test_m6_*.py`（78 项全绿）
