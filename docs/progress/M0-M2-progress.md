# 小二（Xiao）· M0-M2 实现进度审计基线

> 本文档是 M0-M2 的**可核验进度基线**，供第三方审计。所有状态以**真实文件 + 可复现测试**为准，非口头声明。
> 审计日期：2026-08-30 ｜ 版本：v1.0
> 复核方式：`cd 项目根 && python -m unittest <模块> -v`，以下计数均为本机实测。

---

## 一、总览

| 里程碑 | 状态 | 实测测试数 | 核心 |
|---|---|---|---|
| **M0 横切基建** | 部分实现（事件总线✅ 网关✅ 宏观四态✅ 授权中心✅ / 传感器⬜） | **121** | 跨模块事件总线 + 出网安全网关 + 宏观四态 + 授权中心 |
| **M1 记忆工程** | 已实现（六个子模块 + 接入对话主链路） | **116** | 五要素 schema + 数据轨 + 冲突协议 + 巩固调度 + 听错分级 + 检索注入 + 人设内容资产 |
| **M2 情感与姿态** | 已实现（后端四模块 + attack 防御话术组；星云前端映射未做） | **115** | 情感状态机 + 八态判定卡 + 话术库（含 attack）+ 影子日志 + 事件接入 |

**合计：352 项单测全绿。**

---

## 二、M0 横切基建

### 2.1 已实现（✅）

#### 跨模块事件总线
- **文件**：`backend/event_bus.py`
- **接口**：`EVENT_TYPES`（frozenset 白名单，32 条事件，含 T7 编排 `task.node_*` 6 条）、`EventBus.on/emit/count/clear`、模块级单例 `bus`
- **特点**：线程安全（Lock）；未知事件名 fail-fast（ValueError）；payload 缺省 `{}`；与前端 `session/state.py` 解耦。
- **契约来源**：`EVENT_REGISTRY.md` §一（唯一事实源）。
- **测试**：`tests/test_event_bus.py`

#### 出网安全网关
- **文件**：`backend/gateway/`（`gateway.py` / `blocklist.py` / `obfuscate.py` / `load_config.py` / `semantic_filter.py` / `session_manifest.py` / `constants.py` / `compliance.yaml`）
- **对外入口**：`guard_outbound(text, session_id)` / `guard_inbound(returned_text, session_id)` / `get_session_context(session_id)`
- **配置**：`compliance.yaml`（顶层键 `compliance_gateway`），字段 `enabled`/`local_only_keywords`/`obfuscation_mapping`/`suggested_entities_max`/`debug_log`
- **机制**：黑词本机拦截（含自伤红线 `SELF_HARM_KEYWORDS`）、人名占位混淆、还原校验、语义消歧（onnxruntime）
- **测试**：`tests/test_gateway.py` / `test_blocklist.py` / `test_obfuscate.py` / `test_semantic_filter.py` / `test_session_manifest.py`

#### 宏观四态状态机（新增 · 已实现 · T5）
- **文件**：`backend/macro_state.py`（模块级单例 `macro_state`）
- **接口**：`MacroStateMachine`：`tick()`（时间驱动 ACTIVE→IDLE→DORMANT）、`on_user_dialogue()`（用户主动对话 DORMANT→RETURNING→ACTIVE）、`is_proactive_allowed()`（DORMANT 冻结闸门）、`is_affect_decay_paused()`（M2 联动钩子，决策 4.5）、`on_background_completion()`（照办不推送）、`regression_brief()`、`returning_greeting()`、`save()/load()`（持久化最后状态）
- **三纪律（DORMANT，安全行为唯一载体）**：主动事件总线零消息；零归因；托付后台任务照办但不推送（进展只进回归简报）
- **事件**：`macro.state_changed`{前态,后态,时长}（状态转换时发布；M3/M2/M6 订阅，DORMANT 触发各方冻结）
- **RETURNING 分层问候**：≤3 天"几天没见"+简报≤2 条｜≤2 周/中间"有一阵子了"+≤3 条+"慢慢来"｜≥2 个月"好久好久不见"+≤1 条+"最近有什么新变化可以告诉我的？"（交还主动权，永不追问去向）；三源简报（系统足迹/小二足迹/lorebook）
- **测试**：`tests/test_macro_state.py`（22 项全绿）

#### 授权中心（新增 · 已实现 · T6）
- **文件**：`backend/authorization.py`（`AUTHORIZATION_ITEMS` / `AuthorizationCenter`）
- **接口**：`get()`（视图，缺项补默认）/ `get_item` / `is_granted` / `is_feature_granted` / `validate` / `set` / `revoke` / `set_feature` / `revoke_feature`
- **登记项（默认全关）**：`camera_enabled`（默认 false）/ `screen_awareness`（默认 false）/ `proactivity_level`（默认 0）/ `emergency_passthrough`（默认 []）/ `per_feature`（默认 {}）
- **设计定位**：授权项**不在** `settings_schema.SCHEMA` 登记——config_guard 的 `allowed_config_paths()` 会把 `authorizations.*` 判为未知路径，天然拒绝经 `/api/config` 改写（提权段保护，与 T2 perms 同源）；写入只走专用 `/api/authorizations/*` 端点；本模块是 config.yaml `authorizations` 段的**唯一写者**（整段替换写回，避免深合并对空 dict 无法清空的语义问题）
- **测试**：`tests/test_authorization.py`（23 项全绿）

#### 注意力传感器（新增 · 已实现 · M3-M0）
- **文件**：`backend/attention.py`
- **信号**：键鼠空闲（GetLastInputInfo，60s 采样，只读 `is_idle()`/`idle_seconds()`）、全屏检测（前台窗口样式 → `attention.fullscreen{on/off,进程名}`）、前台进程名（GetForegroundWindow 查询即用零留存）、系统负载（`system_load()`/`load_tier()` 只读）、叹气启发式（`classify_sigh` 三阶段：硬门 → 键鼠二元校准 → 30s 平滑）
- **进程黑名单硬阻断**：`guard_blacklisted_window()` 挂载工具分发层（`tools/computer.py` 鼠标/看屏 + `tools/system_control.py` 截图），游戏/网银/支付前台 → 拒绝话术「这个窗口我不看也不动，放心。」（fail-closed，**不门控**，只查进程名元数据零留存）
- **隐私门控**：`screen_awareness`（默认 false）总闸门，关闭时 `sample_window/emit_fullscreen/emit_sigh/tick` 均不采集不发布（fail-closed）；`SighCollector` 只聚合统计、零留存原始音频/键值
- **事件**：`attention.fullscreen` / `attention.sigh`（EVENT_REGISTRY 已预登记，本模块仅发布）——**未新增事件、未改白名单**
- **测试**：`tests/test_attention.py`（22 项全绿）；复核 `python -m unittest discover -s tests -p test_attention.py -v`

### 2.2 待实现（⬜，设计态，规格在 `docs/specs/M0-core.md`）

| 子模块 | 状态 | 说明 |
|---|---|---|
| （无待实现项） | ✅ | 注意力传感器已随 M3-M0 实现，见 §2.1 |

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
| 话术库 | `backend/memv2/phrases.py` + `prompts/` | `load_phrases`/`pick`/`all_ban_words`；6 个 YAML（35 条），含「记岔了，你提醒得对」；**T9a 新增 attack 防御话术组**（`prompts/attack.yaml`，6 条，应对言语攻击/脏话/歧视侮辱） |
| 影子日志 | `backend/memv2/shadow.py` | `ShadowLog.record/get_entries`；**只记录不切换**（守卫抛 AssertionError） |

- **测试**：`tests/test_memv2_posture.py` / `test_memv2_affect.py` / `test_memv2_phrases.py` / `test_memv2_shadow.py` / `test_memv2_bridge.py`

### 待办（⬜）
- **M2-E 星云情绪映射（前端）**：后端 `get_visual_state()` 已产出 hue/brightness/flow_speed，但 `frontend/src/components/Nebula.tsx` **尚未订阅** `affect.updated`/视觉映射。属增强项，不阻塞后端功能。**需在视觉方向确认后实施。**

---

## 五、未做/后置清单（供规划）

| 项 | 里程碑 | 优先级 | 类型 |
|---|---|---|---|
| M0 注意力传感器 | M0 | P1 | ✅ M3-M0 已实现（`backend/attention.py`），见 §2.1 |
| M1 画像层持久化完善 | M1 | P1 | 增强 |
| M2-E 星云前端映射 | M2 | P1 | 后置增强 |
| M2 建设性冲突三层周期完整实现 | M2 | P2 | 后置（完整规格） |
| M3-M6（主动/视觉/物理/成长） | M3-M6 | P2 | 后置增强 |

---

## 六、审计验证命令

```powershell
cd 项目根
python -m unittest tests.test_event_bus tests.test_macro_state tests.test_authorization tests.test_gateway tests.test_blocklist tests.test_obfuscate tests.test_semantic_filter tests.test_session_manifest  # M0: 121
python -m unittest tests.test_memv4 tests.test_memv1_conflict tests.test_memv1_consolidate tests.test_memv1_mishearing tests.test_memv1_retrieval tests.test_memv1_persona  # M1: 116
python -m unittest tests.test_memv2_posture tests.test_memv2_affect tests.test_memv2_phrases tests.test_memv2_shadow tests.test_memv2_bridge tests.test_memv2_attack  # M2: 115
python -m unittest tests.test_attention tests.test_m3_budget tests.test_m3_heartbeat tests.test_m3_aggregate tests.test_m3_event_trigger tests.test_m3_dormant tests.test_m3_anniversary tests.test_m3_style tests.test_m3_shadow  # M3-M0..M3-M6 增量: 22+25+20+24+32+9+6
python scripts/audit_module_boundaries.py   # T0: 模块边界纪律断言（S6），期望 PASS 0告警；--strict 可把待复核视为违规
python -m unittest tests.test_tts_timeout              # T1: 全链路超时兜底（C4/C5），期望 OK
python -m unittest tests.test_audit_remediation         # T2/T3+R1/R2/R3: 配置提权/open_app白名单/审计限频脱敏/简报熔断，期望 OK
python -m unittest tests.test_event_bus tests.test_backup # T4: 事件持久化+有界队列+备份(SHA-256/保留7天)，期望 OK
python scripts/backup.py --help                         # T4: 备份脚本 CLI（每日快照 logs/ 数据目录）
```

**注意**：M1 的 `memv4`/M1-F 部分测试在写入临时目录时可能受受限环境（如沙盒）限制；数据落盘逻辑已按真实路径（`ROOT/persona/` 等）验证正确，正常用户机器无此限制。
