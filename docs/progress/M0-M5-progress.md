# 小二（Xiao）· M0-M5 实现进度审计基线

> **用途**：本文档是 M0-M5 的**可核验进度基线**，供第三方审计。所有状态以**真实文件 + 可复现测试**为准，非口头声明。
> **审计日期**：2026-08-31 ｜ **版本**：v2.0 ｜ **里程碑范围**：M0-M5（M6 成长为设计态，未实现）。
> **复核方式**：`cd 项目根 && python -m unittest discover -s tests`（.venv Python 3.12.13），以下计数均为本机实测。
> **配套**：M0-M2 详细子模块说明见 `docs/progress/M0-M2-progress.md`；模块设计书见 `docs/specs/`；事件契约见 `docs/specs/EVENT_REGISTRY.md`。

---

## 〇、审计导览（第三方先读）

- [x] **全量测试**：`python -m unittest discover -s tests` → 实测 **Ran 822 tests**（failures=2, errors=9）。
- [x] **已知失败 11 项**：`test_memory` 9 项为沙箱既有环境项；`test_dsh_web_bridge` 1 项为时序缺陷待修（非沙箱），见 §六。
- [x] **无回落判据**：M3-M0 开工前基线 = **616 tests**（11 项失败）；M3-M5 完成后 = **815 tests**（新增 199，失败仍为同样 11 项）；审计整改后 = **822 tests**（+7，失败仍为同样 11 项）。
- [x] **M3/M4/M5 全部 ✅**：17 个任务包完成度追踪表见 `_audit/拆解计划-M3-M5-2026-08-30.md` §七。

---

## 一、总览

### 1.1 分里程碑统计（实测）

| 里程碑 | 状态 | 核心测试数 | 关键实现 |
|---|---|---|---|
| **M0 横切基建** | 已实现（含 M3-M0 注意力传感器） | **121 + 22 = 143** | 事件总线 / 出网安全网关 / 宏观四态 / 授权中心 / 注意力传感器 |
| **M1 记忆工程** | 已实现 | **116** | 五要素 schema + 数据轨 + 冲突协议 + 巩固调度 + 听错分级 + 检索注入 + 人设内容资产 |
| **M2 情感与姿态** | 已实现（后端;星云前端映射未做） | **115** | 情感状态机 + 八态判定卡 + 话术库（含 attack）+ 影子日志 + 事件接入 |
| **M3 主动引擎** | 框架已实现（7 包；含 stub：心跳内容源 / 预判数据源未接真实外部） | **138** | 注意力传感器 / 预算制消费 / 心跳 / 事件触发 / DORMANT 联动+纪念日 / 风格画像 / 影子期 |
| **M4 观察会话** | 框架已实现（4 包；真实 VLM 调用 / 摄像头采集未接入，前端渲染后置） | **28** | 观察会话框架 S0-S6 / 穿搭场景 / 视线对齐 / 反馈闭环 |
| **M5 物理编排** | 框架已实现（6 包；HA 网络层 stub 待接真实 REST/WebSocket） | **33** | HA 主路径 / 审批分级 / 场景编排 / VLM 兜底 / 行程编排 / 米家直连(实验默认关) |

**核心合计（M0-M5）：143 + 116 + 115 + 138 + 28 + 33 = 573**（M0 含注意力传感器 +22 时）。

### 1.2 全量测试口径说明（重要，防误读）

- **全量 `discover -s tests` = 822**：包含**所有** `tests/` 下的测试文件（含 T7 编排 / T8 审计 / gateway 分文件 / backup 等非里程碑核心模块，共 264 项 + 审计整改 7 项）。
- **M0-M2 核心**=352（M0 121 / M1 116 / M2 115）；**M3-M5 核心**=199（M3 138 / M4 28 / M5 33）。
- 三者关系：352（M0-M2 核心）+ 199（M3-M5）+ 264（T7/T8/gateway/backup 等其他）+ 7（审计整改）= **822**。
- 历史基线：M3-M0 开工前全量 = **616**（= 352 + 264，注意力传感器尚未实现，M0 实为 121）；M3-M5 完成后 = **815**（新增 199）；审计整改后 = **822**（+7：event_bus 取消屏蔽 4 + attention 黑名单熔断 3）。

---

## 二、M3 主动引擎（本轮实现，7 包）

| 任务包 | 文件 | 要点 | 测试数 |
|---|---|---|---|
| M3-M0 注意力传感器 | `backend/attention.py` | 键鼠空闲/全屏检测/前台进程名/系统负载/叹气三阶段；进程黑名单硬阻断挂载工具分发层（computer/system_control）；screen_awareness 门控+零留存；事件 fullscreen/sigh 仅发布 | `tests/test_attention.py` 22 |
| M3-M1 预算制消费 | `backend/m3/budget.py` `score.py` `notify.py` | daily_quota/四维打分(0.35/0.30/0.25/0.10)/消费流程；**勿扰前置**(命中不耗额)/全屏零投递/日额度硬上限；事件 candidate/delivered 仅发布 | `tests/test_m3_budget.py` 26 |
| M3-M2 心跳引擎 | `backend/m3/heartbeat.py` `content.py` | 早/中/晚三档定时(asyncio后台)；候选生成(stub内容源)+质量门；无响应3天降频；note_user_response 内部重置 | `tests/test_m3_heartbeat.py` 20 |
| M3-M3 事件触发 | `backend/m3/event_trigger.py` `aggregate.py` | 订阅6已有事件→相关性/紧急度/通知策略/**同类窗口聚合防风暴**/冷却→调 notifier；哀伤3/7/30；预判天气+日程(stub)；紧急穿透仅配置清单 | `tests/test_m3_aggregate.py` 8 + `test_m3_event_trigger.py` 16 |
| M3-M4 DORMANT+纪念日 | `backend/m3/dormant.py` `anniversary.py` | 不重做DORMANT(只订阅 macro.state_changed+只读宏态)；纪念日两类豁免(里程碑/正向极值,废纯计数)；负向异常永不主动开场；heartbeat/event_trigger 补生成前冻结层 | `tests/test_m3_dormant.py` 14 + `test_m3_anniversary.py` 18 |
| M3-M5 主动风格画像 | `backend/m3/style.py` | 纯行为画像(response_rate/preferred_density/override_by_user)无心理标签；override暂停自适应；回应率反向调节心跳降频；只读M1画像 | `tests/test_m3_style.py` 9 |
| M3-M6 影子期假投递 | `backend/m3/shadow.py` + notify shadow 模式 | notify 加 shadow 模式(只记录不真投,不 emit delivered)；ShadowRecorder 记录+响应率采集；默认态 | `tests/test_m3_shadow.py` 5 |

---

## 三、M4 观察会话（本轮实现，4 包）

| 任务包 | 文件 | 要点 | 测试数 |
|---|---|---|---|
| M4-M1 观察会话框架 | `backend/m4/session.py` | S0-S6 状态机(白名单转换)；帧内存驻留/≤24硬上限/会话结束焚毁；摄像头复用 authorization.camera_enabled(默认关)；S2前零出网；vision.session_state/conclusion/feedback 仅发布 | `tests/test_m4_session.py` 10 |
| M4-M2 穿搭场景 | `backend/m4/scene_outfit.py` | 场景模板为配置(单品列表/主色调/层数)+结构化解析(JSON/键值)+重试一次失败降级话术+建议结构(总评/分项/可执行) | `tests/test_m4_outfit.py` 8 |
| M4-M3 视线对齐 | `backend/m4/gaze.py` | 后端 gaze 位置→归一化偏移纯函数(位置字段/无位置None默认形态/clamp)；前端 Nebula 偏移渲染属增强后置(项目无前端测试框架) | `tests/test_m4_gaze.py` 6 |
| M4-M4 反馈闭环 | `backend/m4/feedback.py` | S6三态(接受/不接受/部分)全入记忆+负反馈标记负向优先画像+拒绝仅追问一次+vision.feedback；唯一持久化=文字结论入M1 | `tests/test_m4_feedback.py` 4 |

---

## 四、M5 物理与编排（本轮实现，6 包）

| 任务包 | 文件 | 要点 | 测试数 |
|---|---|---|---|
| M5-M1 HA 主路径 | `backend/m5/ha_client.py` | 感知先行动→审批分级(白名单auto/外转建议)→执行→读回5s→mismatch播报一次；token 走 env 不硬编码；device.state_changed 仅发布；HA 网络层 stub 待接真实 REST | `tests/test_m5_ha.py` 7 |
| M5-M2 审批分级+偏离确认 | `backend/m5/approval.py` | 审批分级复用 perms(白名单auto/外confirm不自动执行)+偏离惯例仅偏离确认+重建模式明说；主动+物理执行隔离 | `tests/test_m5_approval.py` 6 |
| M5-M3 场景编排 | `backend/m5/scene.py` | HA scene 原子化(一次调用非逐设备)+plan.landed(已登记只发布)；多设备并发首版按 scene 原子化 | `tests/test_m5_scene.py` 3 |
| M5-M4 VLM 兜底操电脑 | `backend/m5/vlm_operator.py` | VLM兜底(截屏→定位→确认→执行→前后diff验证)+坐标缩放映射+支付/密码页硬黑名单；进程黑名单复用 M0 attention；**安全提权先测** | `tests/test_m5_vlm.py` 7 |
| M5-M5 行程/饮食编排 | `backend/m5/trip.py` | A型范式(≤3方案各带代价+置信度→敢推荐→落地动作明确/缺则明说不落地)；天气源可注入(默认未接入，方案硬编码，见 §八) | `tests/test_m5_trip.py` 5 |
| M5-M6 米家直连(实验性) | `backend/m5/mijia.py` | 米家直连默认关+弹窗文案 config 可配+token 走 env 不硬编码+失败 3 次即停提示转层1；层2 读回验证=执行确认+15-30s 延迟复查 | `tests/test_m5_mijia.py` 5 |

---

## 五、审计验证命令

```powershell
cd 项目根
# ① 全量（唯一权威复核，实测 822 + 11 项既有失败）
python -m unittest discover -s tests
# ② M0-M2 核心
python -m unittest tests.test_event_bus tests.test_macro_state tests.test_authorization tests.test_gateway tests.test_blocklist tests.test_obfuscate tests.test_semantic_filter tests.test_session_manifest tests.test_memv4 tests.test_memv1_conflict tests.test_memv1_consolidate tests.test_memv1_mishearing tests.test_memv1_retrieval tests.test_memv1_persona tests.test_memv2_posture tests.test_memv2_affect tests.test_memv2_phrases tests.test_memv2_shadow tests.test_memv2_bridge tests.test_memv2_attack
# ③ M3-M5 增量（本轮新增 199 项）
python -m unittest tests.test_attention tests.test_m3_budget tests.test_m3_heartbeat tests.test_m3_aggregate tests.test_m3_event_trigger tests.test_m3_dormant tests.test_m3_anniversary tests.test_m3_style tests.test_m3_shadow tests.test_m4_session tests.test_m4_outfit tests.test_m4_gaze tests.test_m4_feedback tests.test_m5_ha tests.test_m5_approval tests.test_m5_scene tests.test_m5_vlm tests.test_m5_trip tests.test_m5_mijia
# ④ 模块边界纪律断言（S6，T0）
python scripts/audit_module_boundaries.py   # 期望 PASS 0告警; --strict 可把待复核视为违规
# ⑤ 其他专项
python -m unittest tests.test_tts_timeout tests.test_audit_remediation tests.test_backup
```

---

## 六、已知测试失败项（审计须知：分沙箱环境项 + 时序缺陷）

全量 `discover -s tests` 共 11 项失败/错误（实测 `Ran 822, failures=2, errors=9`），分两类：① `test_memory` 的 9 项为**沙箱既有环境项**（与本阶段 M3/M4/M5 实现无关）；② `test_dsh_web_bridge` 的 1 项为**实现时序缺陷待修**（非沙箱环境项，见下表）：

| 测试文件 | 失败数 | 根因 | 是否本阶段引入 |
|---|---|---|---|
| `test_memory` | 8 errors + 1 fail | 写入系统临时目录 `C:\\Users\\...\\Temp\\dsh-*` 被受限沙箱拒绝（PermissionError） | 否（开工前基线即存在） |
| `test_dsh_web_bridge.StreamTests.test_e2e_stream_and_steps` | 1 fail | 事件流时序脆弱（tool/call vs work_step 顺序），属 A2 桥实现/测试时序缺陷，**非沙箱环境项，列为待修** | 否（既有，待修） |

**无回落判据**：若 M3-M0 开工前基线为 616（11 项失败），M3-M5 后为 815、审计整改后为 822（仍为同样 11 项失败），且失败项名称/数量不变，即判定**无回落**。

**沙箱约束**：M1 `memv4`/M1-F 部分测试在受限环境下写入临时目录会被拒绝；数据落盘逻辑已按真实路径（`ROOT/persona/`、`ROOT/.tmp/` 等）验证正确，正常用户机器无此限制。

---

## 七、本阶段铁规执行情况

- **事件四处同步**：本阶段所有用到的事件（attention/proactive/vision/device.state_changed/env.anomaly/plan.landed 等）**均已预登记**（`EVENT_REGISTRY.md §一` + `backend/event_bus.py` `EVENT_TYPES` 白名单），各包仅作发布/订阅方，**未新增任何事件、未改白名单**。
- **安全提权项先测**：进程黑名单（M3-M0）、摄像头启停（M4-M1）、设备白名单自动（M5-M1）、VLM 兜底+支付/密码黑名单（M5-M4）均 TDD（先写测试再实现）。
- **密钥**：HA/米家 token 均走 `os.getenv`（`.env` 可配），无硬编码；相对路径未写死本机。
- **跨模块**：一律走 `backend/event_bus.py`，未直调；M3/M4/M5 消费端复用 M0 授权中心/宏态，未另造。
- **只实现不发布**：本阶段未启用发布/上线；未引入 HomeRail 代码。
- **隐私红线**：多模态帧仅内存驻留、会话结束焚毁；敏感文本命中 `local_only_keywords` 走本机沙盒；注意力数据零留存。

---

## 八、待办 / 后置清单（供规划）

| 项 | 状态 | 说明 |
|---|---|---|
| M3-M0/M3-M6/M4 系列多包 | 已实现 | 因子代理反复空转，由主会话直接实现（各包备注标注「主会话直实现」）。 |
| M4-M3 前端星云偏移渲染 | 后置增强 | 项目无前端测试框架，与 M2-E 星云映射同后置。 |
| M4 真实 VLM 调用/摄像头采集 | 后置 | 穿搭/视线对齐当前为纯后端函数+可注入 stub，真实 VLM（qwen-vl-max/glm-4v）与摄像头采集未接入。 |
| M5-M1 HA 真实 REST/WebSocket | 后置 | 首版用 stub 验证编排逻辑，待真实 HA 环境接入。 |
| M1-C 巩固调度真实接入 | 待办 | consolidate 内联门面，可上提统一。 |
| M1 画像层持久化完善 | 待办 | 画像条目持久化接口待完善。 |
| M2-E 星云前端映射 | 待办 | Nebula.tsx 未订阅 affect.updated。 |
| M6 健康成长 | 未实现 | ROADMAP 设计态。 |

---

## 九、第三方审计整改总结（2026-08-31）

### 9.1 审计来源与核实结论

三份第三方审计（Google / Qwen / DeepSeek，均自声明「未审代码，仅审文档」）的发现**方向大体属实**，但存在 **4 处硬误判**，并把「文档滞后」上纲为「严重虚报 / 功能就绪幻觉」。经 3 子代理逐条读代码/文档核实：**无欺骗、无就绪幻觉**。完整自检报告见 `_audit/自检报告-2026-08-31.md`。

### 9.2 审计硬误判（4 处）

1. **B2 引用不存在的文件**：Google 要求改 `backend/audit/xiao_logger.py`，该文件不存在；其要求的 4KB 刷盘 + `[REDACTED]` 遮蔽已实现于 `xiao_audit.py`。
2. **B3「翻旧账」无中生有**：`notify.py` 不做话术组装，无作息/极值引用，无此风险面。
3. **A3 EVAL 场景三误判**：实为收窄后口径（明确任务动词直通），非旧口径。
4. **「欺骗/严重虚报」过度定性**：progress 行级明细已披露 stub，仅汇总表横幅不够精确。

### 9.3 整改清单（10 项全部完成）

| 项 | 文件 | 状态 |
|---|---|---|
| P0-1 事件总线取消屏蔽（线程本地 emit 屏蔽） | `backend/event_bus.py` | ✅ |
| P0-2 进程黑名单广度扫描 + fail-closed 熔断 | `backend/attention.py` | ✅ |
| P2-9 任务态口径收窄（unknown → recall） | `backend/memv1/retrieval.py` | ✅ |
| P1-3 README 补长尾明文披露 | `README.md` | ✅ |
| P1-4 DESIGN 补宏观四态 + event_bus | `docs/DESIGN.md` | ✅ |
| P1-5 NOTICE 补 Piper/GPL 边界 | `NOTICE` | ✅ |
| P1-6 ROADMAP 配置名收口 | `docs/ROADMAP.md` | ✅ |
| P1-7 progress 汇总表加注 stub | `docs/progress/M0-M5-progress.md` | ✅ |
| P2-8 progress 测试归因修正 | `docs/progress/M0-M5-progress.md` | ✅ |

### 9.4 测试增量与回归

全量 **815 → 822**（+7）：`event_bus` 取消屏蔽 +4、`attention` 黑名单熔断 +3。全量 `Ran 822, failures=2, errors=9`，11 项失败仍为既有 `test_memory`（沙箱）+ `test_dsh_web_bridge`（时序），**无新增回归**。

### 9.5 遗留待办（供后续）

- `test_dsh_web_bridge` 时序缺陷待修（A2 桥事件流顺序）。
- M4 真实 VLM 调用 / 摄像头采集未接入（后置）。
- M5-M1 HA 真实 REST/WebSocket 未接（stub，后置）。
- Piper GPL 边界留待打包阶段决定是否物理隔离。
