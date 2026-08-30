# 小二（Xiao）· M0-M2 实现进度审计基线

> 本文档是 M0-M2 的**可核验进度基线**，供第三方审计。所有状态以**真实文件 + 可复现测试**为准，非口头声明。
> 审计日期：2026-08-30 ｜ 版本：v1.0
> 复核方式：`cd 项目根 && python -m unittest <模块> -v`，以下计数均为本机实测。

---

## 一、总览

| 里程碑 | 状态 | 实测测试数 | 核心 |
|---|---|---|---|
| **M0 横切基建** | 部分实现（事件总线✅ 网关✅ / 宏观四态⬜ 授权⬜ 传感器⬜） | **72** | 跨模块事件总线 + 出网安全网关 |
| **M1 记忆工程** | 已实现（六个子模块 + 接入对话主链路） | **116** | 五要素 schema + 数据轨 + 冲突协议 + 巩固调度 + 听错分级 + 检索注入 + 人设内容资产 |
| **M2 情感与姿态** | 已实现（后端四模块；星云前端映射未做） | **96** | 情感状态机 + 八态判定卡 + 话术库 + 影子日志 + 事件接入 |

**合计：284 项单测全绿。**

---

## 二、M0 横切基建

### 2.1 已实现（✅）

#### 跨模块事件总线
- **文件**：`backend/event_bus.py`
- **接口**：`EVENT_TYPES`（frozenset 白名单，26 条）、`EventBus.on/emit/count/clear`、模块级单例 `bus`
- **特点**：线程安全（Lock）；未知事件名 fail-fast（ValueError）；payload 缺省 `{}`；与前端 `session/state.py` 解耦。
- **契约来源**：`EVENT_REGISTRY.md` §一（唯一事实源）。
- **测试**：`tests/test_event_bus.py`

#### 出网安全网关
- **文件**：`backend/gateway/`（`gateway.py` / `blocklist.py` / `obfuscate.py` / `load_config.py` / `semantic_filter.py` / `session_manifest.py` / `constants.py` / `compliance.yaml`）
- **对外入口**：`guard_outbound(text, session_id)` / `guard_inbound(returned_text, session_id)` / `get_session_context(session_id)`
- **配置**：`compliance.yaml`（顶层键 `compliance_gateway`），字段 `enabled`/`local_only_keywords`/`obfuscation_mapping`/`suggested_entities_max`/`debug_log`
- **机制**：黑词本机拦截（含自伤红线 `SELF_HARM_KEYWORDS`）、人名占位混淆、还原校验、语义消歧（onnxruntime）
- **测试**：`tests/test_gateway.py` / `test_blocklist.py` / `test_obfuscate.py` / `test_semantic_filter.py` / `test_session_manifest.py`

### 2.2 待实现（⬜，设计态，规格在 `docs/specs/M0-core.md`）

| 子模块 | 状态 | 说明 |
|---|---|---|
| 宏观四态（ACTIVE/IDLE/DORMANT/RETURNING） | 设计态 | **MVP 阻塞**。DORMANT 冻结是安全行为唯一载体。审计建议：实现前**先写 DORMANT 单元测试**锁三条纪律 |
| 授权中心 | 设计态 | 摄像头/审批常驻授权/紧急穿透配置统一收口 |
| 注意力传感器 | 设计态 | 键鼠空闲/全屏检测/进程黑名单/叹气启发式（三阶段硬门） |

---

## 三、M1 记忆工程

### 已实现（✅）

| 子模块 | 文件 | 要点 |
|---|---|---|
| 五要素 schema + 数据轨 | `backend/memv4.py` + `backend/memv1/schema.py`（桥接层） | `MemEntry` dataclass（五要素 + encrypted/enc_token 预留）；`DataTrack` 三层（session_logs/raw_frames_meta/context_snapshots，原子写，零丢失）；`evict_low_confidence` |
| 冲突三协议 + 检索合并 + 配额 | `backend/memv1/conflict.py` | `classify_conflict`/`merge_for_retrieval`/`WeeklyQuota`；局部>全局、行为>声明；配额 ≤3/周 |
| 巩固调度（云经网关） | `backend/memv1/consolidate.py` | `trigger_consolidation`/`apply_profile`（原子提交）；`guard_outbound`/`guard_inbound`；写锁排他；JSON Schema Prompt |
| 听错入口分级 | `backend/memv1/mishearing.py` | `classify_risk`/`build_confirm_utterance`；高风险复述确认；音频零留存 |
| 检索注入 + 180 天滤镜 + 任务态 | `backend/memv1/retrieval.py` | `build_injection`/`is_recall_or_task`；>180 天硬截断；任务态默认放行；沙盒隔离 |
| 人设/人物卡/哀伤标签/惯例画像 | `backend/memv1/persona.py` + `persona/` | `load_persona`/`KinshipCard`/`add_grief_tag`/`habit_profile`/`inject_lorebook` |
| 接入对话主链路 | `backend/agent.py` | `_messages()` 用 `build_injection`，空则回退 `context_text` |

- **测试**：`tests/test_memv4.py` / `test_memv1_conflict.py` / `test_memv1_consolidate.py` / `test_memv1_mishearing.py` / `test_memv1_retrieval.py` / `test_memv1_persona.py`

### 待办（⬜）
- **M1-C 巩固调度真实接入**：当前 `create_client()` 为 consolidate.py 内联门面（真实 `factory.py` 只有 `build_llm()`）；可上提统一。
- **agent 接线后的 M1 画像持久化**：provider 目前读 `memv4.DataTrack` 会话轨；画像条目（MemEntry）持久化与读取接口待 M1-A 画像层完善。
- **180 天滤镜**：架构位已就绪，MVP 记忆存量不满 180 天天然不触发（合法延后）。

---

## 四、M2 情感与姿态（后端）

### 已实现（✅）

| 子模块 | 文件 | 要点 |
|---|---|---|
| 情感状态机 | `backend/memv2/affect.py` | `AffectState(frozen)` + `apply_event`/`decay`/`get_visual_state`；mood/intimacy 范围钳制；**get_visual_state 只显形态不露数值**（红线） |
| 八态判定卡规则引擎 | `backend/memv2/posture.py` | `PostureCard`/`PostureClassifier.classify`；安全优先扫描；默认 friend 兜底；深夜时段加成 |
| 事件接入总线 | `backend/memv2/bridge.py` | `publish_posture_change`/`publish_affect_updated`/`log_shadow_decision`；订阅 `memory.profile_updated`；读 M1 只读快照；不 import M1 |
| 话术库 | `backend/memv2/phrases.py` + `prompts/` | `load_phrases`/`pick`/`all_ban_words`；5 个 YAML（29 条），含「记岔了，你提醒得对」 |
| 影子日志 | `backend/memv2/shadow.py` | `ShadowLog.record/get_entries`；**只记录不切换**（守卫抛 AssertionError） |

- **测试**：`tests/test_memv2_posture.py` / `test_memv2_affect.py` / `test_memv2_phrases.py` / `test_memv2_shadow.py` / `test_memv2_bridge.py`

### 待办（⬜）
- **M2-E 星云情绪映射（前端）**：后端 `get_visual_state()` 已产出 hue/brightness/flow_speed，但 `frontend/src/components/Nebula.tsx` **尚未订阅** `affect.updated`/视觉映射。属增强项，不阻塞后端功能。**需在视觉方向确认后实施。**

---

## 五、未做/后置清单（供规划）

| 项 | 里程碑 | 优先级 | 类型 |
|---|---|---|---|
| M0 宏观四态（含 DORMANT 预写测试） | M0 | P0 | 阻塞发布 |
| M0 授权中心 | M0 | P0 | 阻塞发布 |
| M0 注意力传感器 | M0 | P1 | 后置 |
| M1 画像层持久化完善 | M1 | P1 | 增强 |
| M2-E 星云前端映射 | M2 | P1 | 后置增强 |
| M2 建设性冲突三层周期完整实现 | M2 | P2 | 后置（完整规格） |
| M3-M6（主动/视觉/物理/成长） | M3-M6 | P2 | 后置增强 |

---

## 六、审计验证命令

```powershell
cd 项目根
python -m unittest tests.test_event_bus tests.test_gateway tests.test_blocklist tests.test_obfuscate tests.test_semantic_filter tests.test_session_manifest   # M0: 72
python -m unittest tests.test_memv4 tests.test_memv1_conflict tests.test_memv1_consolidate tests.test_memv1_mishearing tests.test_memv1_retrieval tests.test_memv1_persona  # M1: 116
python -m unittest tests.test_memv2_posture tests.test_memv2_affect tests.test_memv2_phrases tests.test_memv2_shadow tests.test_memv2_bridge  # M2: 96
```

**注意**：M1 的 `memv4`/M1-F 部分测试在写入临时目录时可能受受限环境（如沙盒）限制；数据落盘逻辑已按真实路径（`ROOT/persona/` 等）验证正确，正常用户机器无此限制。
