# M0-core · 横切基建 · 模块设计书

```yaml
module: M0-core
version: v4.1.1        # 对应 ROADMAP v4.1；本版新增「跨模块事件总线」落地 + 按代码现状校准
status: partial        # 部分实现：出网网关/事件总线/宏观四态（T5）/授权中心（T6）已落地；注意力传感器为设计态待实现
depends_on: []         # 全系统地基，无前置模块；依赖现有底座见 §2（其中事件总线已新增）
paradigm: C/E 混合     # 后台守护（注意力/巩固调度）+ 物理安全层（网关）
owner_notes: 迁自 ROADMAP §4.M0 + §M0.1/2/3 + §7 资源策略
revision_notes: |
  v4.1.1（本版）：
    - 新增 §3.0 跨模块事件总线（backend/event_bus.py）——与前端用的 session/state.py 解耦，
      为全系统唯一「模块间通信」信道；EVENT_REGISTRY 是其唯一事实源。
    - 按代码现状校准：出网网关状态 approved→已实现；配置文件名 compliance_config.yaml→compliance.yaml；
      网关依赖不再「零第三方库」（语义消歧用 onnxruntime+numpy）；「全模块解耦唯一通道」修正为「事件总线」。
    - status: approved→partial（区分已实现与设计态），后续 M1-M6 对齐。
```

## 1. 目的与范围

为全部模块提供公共地基：**跨模块事件总线**、宏观在场状态机、会话栈、授权中心、偏好学习引擎、注意力传感器、失败礼仪、**出网安全网关**。任何模块实现前，本模块的对应能力必须先行。**

**模块间通信（写死）**：一切跨模块通信只走本模块 §3.0 事件总线（发布事件+订阅事件+只读快照），**禁止跨模块直接调用对方核心函数**。EVENT_REGISTRY.md 是唯一事实源。

**明确不做**：全局键盘钩子/击键动力学（杀软误报+隐私嫌疑+低收益，三重否决，永不重试）；业务功能（记忆/情感/主动等各自在 M1-M6）。

## 2. 前置依赖（现有底座 + 新增）

| 底座 | 位置 | 复用点 |
|---|---|---|
| **跨模块事件总线（新增，已实现）** | `backend/event_bus.py` | **全模块间通信的唯一信道**（模块间禁止直调） |
| 前端会话状态事件流 | `backend/session/state.py` | 只给前端 WebSocket 显示状态（与事件总线解耦，勿混淆） |
| 配置注册表 | `backend/settings_schema.py` | 资源档位、授权项、传感器开关 |
| 后台任务 | `backend/tasks.py` | 巩固/心跳调度的执行容器 |
| 健康探测 | 现有四环节状态灯 | 服务可用性探测复用同一机制 |
| 出网安全网关（新增，已实现） | `backend/gateway/` | 见 §4.3；对外 `guard_outbound/guard_inbound/get_session_context` |

外部依赖：出网网关**非零第三方库**——`semantic_filter` 用 `onnxruntime`+`numpy`（requirements 已含）；黑词/混淆/编排为纯本地正则+替换。

## 3.0 跨模块事件总线（新增 · 已实现 · 全系统唯一通信信道）

- **职责**：模块间发布/订阅语义化事件（`module.event` 点分命名）。M1 发 `memory.profile_updated` → M2 收；M2 发 `posture.changed` → M1 收；如此类推。两个模块无需互相认识、无需直调。
- **实现**：`backend/event_bus.py`。
  - `EVENT_TYPES`：事件名白名单（frozenset），**单一来源 = `EVENT_REGISTRY.md` §一**；新增事件须先登记注册表再补进白名单，否则发布/订阅立刻 fail-fast（ValueError）。
  - `bus = EventBus()`（模块级单例，线程安全）：`on(event_type, handler)` 返回取消订阅函数；`emit(event_type, payload)` 广播；payload 缺省为 `{}`；单 handler 异常不影响其它 subscriber（与 state.py 同模式）。
- **与 `session/state.py` 的分工（写死）**：
  | 信道 | 用途 | 订阅者 |
  |---|---|---|
  | `backend/event_bus.py` | 跨模块语义化事件（M1→M2 等） | 各模块 |
  | `backend/session/state.py` | 前端会话状态事件流（state/assistant_result） | 前端 WebSocket |
  两者**独立**，禁止混用——前端状态流不得承载跨模块事件，反之亦然。
- **MVP 边界**：本模块只做「基础设施 + 事件名注册表 + 按类型过滤订阅」；**不做 payload schema 校验**（交给各订阅模块自行校验），避免 M0 大而全拖慢 MVP。

## 3. 数据结构

```yaml
# 宏观状态（内存单例，持久化最后状态供回归判定）【已实现 · backend/macro_state.py，T5】
macro_state:
  state: ACTIVE | IDLE | DORMANT | RETURNING
  last_interaction: datetime          # 最后一次任意交互时间
  dormant_since: datetime | null

# 授权中心（config.yaml + 运行时覆盖，统一登记）【已实现 · backend/authorization.py，T6】
authorizations:
  camera_enabled: bool                # 默认 false
  screen_awareness: bool              # 默认 false
  proactivity_level: 0-100            # 总闸门滑块
  emergency_passthrough: [清单]       # 用户可配"什么算紧急"
  per_feature: {}                     # 各功能细项授权的注册表

# 资源档位
performance_profile:
  tier: slim | standard | power       # 首次向导硬件探测定初值，用户可改
  hardware: { ram_gb, has_gpu }       # 探测快照
  derating: { nebula_fps, proactive_budget_mult, max_concurrent }
```

## 4. 状态机 / 流程

### 4.1 宏观四态（顶层）【已实现 · backend/macro_state.py，T5】

```
ACTIVE（有交互）─空闲>15min─►IDLE ─无交互7天─►DORMANT ─任意交互─►RETURNING ─新交互─►ACTIVE
```

- **DORMANT 纪律（写死）**：主动引擎冻结非降频；零消息零归因（"用户是不是讨厌我了"禁止）；允许项仅日历提醒/闹钟/HA 传感器异常告警；托付的后台任务照常办**但不推送**（进展只进回归简报）
- **RETURNING 分层问候**：≤3 天"几天没见"+简报≤2 条｜≤2 周"有一阵子了"+≤3 条+"慢慢来"｜≥2 个月"好久好久不见"+≤1 条+"最近有什么新变化可以告诉我的？"（主动权交还）。**永不追问去向**
- **回归简报三源**：源 A 系统足迹（Git 提交/HA 记录/系统日志）｜源 B 小二自身足迹（仅点缀）｜源 C `persona/lorebook/` 抽取（MVP 预置 5-10 条硬编码可编辑条目，见开放问题 1）

### 4.2 注意力传感器

| 信号 | 实现 | 消费者 |
|---|---|---|
| 键鼠空闲 | GetLastInputInfo，60s 一次 | IDLE 判定/巩固触发/勿扰 |
| 全屏检测 | 前台窗口样式查询 | 主动引擎冻结挂起 |
| 前台进程名 | GetForegroundWindow，调用时查询 | 进程黑名单硬阻断 |
| 系统负载 | CPU/内存采样 | 档位自适应 |
| **叹气启发式** | KWS 音频流能量/音高（v4 三阶段：≥2 周影子测试硬门→键鼠二元判断无感校准→30s 色相位移 ≤5% 平滑上线） | **仅星云静默守护形态，禁止触发主动决策或打断** |

**进程黑名单硬阻断（写死）**：游戏/网银/支付类进程前台时，VLM 截屏与鼠标模拟在**工具分发层直接拒绝授权**（防反外挂误判封号+防网银截屏）。拒绝话术："这个窗口我不看也不动，放心。"

### 4.3 出网安全网关（生命线，所有云调用前强制层）【已实现】

> **实现状态**：已落地于 `backend/gateway/`（blocklist / obfuscate / load_config / semantic_filter / session_manifest / gateway.py）。对外三个入口（契约，见 `_M0-tasks/A5-gateway-orchestration.md`）：`guard_outbound(text, session_id)` → `("blocked"|"cloud_safe", processed)`；`guard_inbound(returned_text, session_id)` → `str`；`get_session_context(session_id)` → `SessionContext`。
>
> **配置契约**：文件 `backend/gateway/compliance.yaml`（顶层键 `compliance_gateway`），字段 `enabled` / `local_only_keywords` / `obfuscation_mapping` / `suggested_entities_max` / `debug_log`。**注意：文档旧稿写 `compliance_config.yaml` 为笔误，实际以 `compliance.yaml` 为准。**

```
[待出网文本] → 黑词表命中？ ─是→ 本机保留（规则摘要或"仅本机存档"提示，零云调用）
                   │否
                   ▼
             占位混淆（obfuscation_mapping 全量替换）→ 出网 → 云端返回 → 还原
                                                              │
                                    还原校验：出网前记录占位符数量清单，
                                    回程比对；不一致 → 结构化日志+降级处理
```

- `compliance.yaml`：`local_only_keywords`（身份证/密码/密钥/**自伤类**——自伤类衔接 M2 红线：本机处理+引导专业帮助，绝不出网）+ `obfuscation_mapping`（"我妈"→`User_Kinship_Mother` 等）
- **还原校验层（v4.1.1 会话绑定，替代全局单例）**：Obfuscation Manifest（混淆清单）**挂载当前会话的 SessionContext 字典**，绑定 `session_id + salt_nonce`；流式 Chunk 出网或多任务并发时，校验按 session 隔离，**禁止全局内存单例**（否则网络序异步交错 race 会高频误报降级）。还原失败可观测（结构化日志：原始占位符列表/返回文本/还原结果），不静默降级
- **实体回收闭环**：M1.2 巩固 Prompt 的 `suggested_entities` → 周配额澄清 → 用户确认 → 双入册（M1.5 人物卡 + 本节混淆表）。**高危干预期间 defer（v4.1.1）**：系统级高危干预为**特权断路器，独立于常规会话栈**——触发即清空当轮常规候选队列，长尾实体回收/记忆澄清**无条件 defer 到下一个 ACTIVE 常规会话**，禁止在干预流内挂起常规锁（否则占位符无限期阻塞，长尾映射大面积瘫痪）
- **诚实声明**：手工映射只护高频实体，长尾明文出网（个人自用豁免），设置页显式说明
- **还原校验层（v4.1.1 会话绑定，替代全局单例）**：Obfuscation Manifest（混淆清单）**挂载当前会话的 SessionContext 字典**，绑定 `session_id + salt_nonce`；流式 Chunk 出网或多任务并发时，校验按 session 隔离，**禁止全局内存单例**（否则网络序异步交错 race 会高频误报降级）。还原失败可观测（结构化日志：原始占位符列表/返回文本/还原结果），不静默降级
- **实体回收闭环**：M1.2 巩固 Prompt 的 `suggested_entities` → 周配额澄清 → 用户确认 → 双入册（M1.5 人物卡 + 本节混淆表）。**高危干预期间 defer（v4.1.1）**：系统级高危干预为**特权断路器，独立于常规会话栈**——触发即清空当轮常规候选队列，长尾实体回收/记忆澄清**无条件 defer 到下一个 ACTIVE 常规会话**，禁止在干预流内挂起常规锁（否则占位符无限期阻塞，长尾映射大面积瘫痪）
- **诚实声明**：手工映射只护高频实体，长尾明文出网（个人自用豁免），设置页显式说明

### 4.4 巩固调度协作规则（与 M1 的接口，写死）

锁屏或无键鼠 >15 分钟触发 M1 增量巩固；执行中用户回来立即中断；未完成段丢弃下窗口重做，已完成段保留。

## 5. 规则与话术

- 失败礼仪全仓规范：一切报错 = 人话原因 + 下一步建议（"缺看图方案，去设置配一个，或装本地 MiniCPM-o"），禁止裸异常堆栈
- **事件总线使用规范（写死）**：跨模块一律走 `backend/event_bus.py` 的 `bus.on/emit`，**禁止跨模块直调核心函数**；事件名必须已在 `EVENT_TYPES` 白名单内（对应 EVENT_REGISTRY §一），发布端写错名 → 立即 ValueError（fail-fast，不静默断链）；payload 不做全局校验，由各订阅模块自行校验
- **同轮单一询问原则（v4.1.1，会话层仲裁，写死）**：主动类询问（记忆澄清/册封/邀约印证/元对话）**同一轮对话只出现一个**；优先级：**高危干预 > 记忆澄清（M1）> 关系类询问（M6 册封 / M2 邀约印证）**——M1 有待澄清条目时先处理记忆确认，M6 册封顺延下轮；防止同轮被问两次（体验灾难）。实施于会话栈
- **全局周询问预算（v4.1.1）**：除轮内规则外，**周内跨模块询问总量 ≤5 次/周**（`inquiry_budget.weekly_max` 可配置），各模块按优先级竞争消耗——详见 `EVENT_REGISTRY.md` §5（高危干预即时不占额；记忆澄清>关系类，冲突时高优先级顶替、低优先级顺延下周）
- CPU 硬指标：用户在场交互空闲态 <1%（巩固窗口与空闲增量单列脚注）
- 注意力传感器数据只做二元/计数判断，**禁止存储原始输入内容**（音频原文/键值序列零留存）

## 6. 模块间接口（事件总线契约表）

> 下表仅列 **M0 作为发布者/订阅者** 的事件；全系统事件总表与预算规则见 `EVENT_REGISTRY.md`。事件名须已登记进 `backend/event_bus.py` 的 `EVENT_TYPES` 白名单。

| 事件名 | payload | 发布者 | 订阅者 | 备注 |
|---|---|---|---|---|
| `macro.state_changed` | `{前态,后态,时长}` | M0 | M3/M2/M6 | DORMANT 触发各方冻结 |
| `attention.fullscreen` | `{on/off, 进程名}` | M0 | M3 | 主动候选挂起 |
| `attention.sigh` | `{置信, 键鼠活跃}` | M0 | M2 | 仅星云形态 |
| `gateway.blocked` | `{词类, 处置}` | M0.2 | 日志 | 高危词统计（不出网） |
| `gateway.entities_found` | `[疑似人名]` | M1.2 巩固 | M0.2/M1.5 | 实体回收闭环入口 |

## 7. 验收断言

- 单元级（已实现）：`tests/test_event_bus.py` 11 项全绿——按类型收发、取消订阅、payload 缺省、未知事件名 fail-fast、单 handler 异常不扩散、`EVENT_TYPES` 覆盖注册表关键事件、事件持久化/有界队列/崩溃重放
- 单元级（已实现）：`tests/test_macro_state.py` **22 项全绿**——三纪律（①DORMANT 主动事件总线零消息 ②零归因 ③托付后台任务照办但不推送）+ ACTIVE/IDLE/DORMANT/RETURNING 四态转换 + `macro.state_changed`{前态,后态,时长} payload + RETURNING 三档模板（≤3天/≤2周/≥2个月）+ 三源简报 + 持久化回环
- 单元级（已实现）：`tests/test_authorization.py` **23 项全绿**——授权项集中登记与默认全关、get/validate/set/revoke/set_feature、is_granted/is_feature_granted、非法值抛 ValueError、整段替换写回（提权段保护）
- 模块级：跨模块通信一律走 `bus`（grep 断言无「跨模块直调对方模块模块函数」的 `import`）；黑词命中时网络层断言出网调用数=0；DORMANT 期间主动事件总线零消息；CPU<1% 断言（交互空闲态）；RETRUNING 三档模板与时长匹配
- ROADMAP §6 总表相关项：网关 100% 覆盖、DORMANT 零归因、无常驻钩子

## 8. 开放问题

1. RETURNING 判定用"任意交互"（含后台任务完成通知的点击）还是"主动对话"？**已定（决策 4.3）**：RETURNING 只由「用户主动发起对话」触发，通知点击/后台完成不算（实现 `backend/macro_state.py` 的 `on_user_dialogue` / `on_non_dialogue_interaction`）。遗留：DORMANT 判定阈值（无交互 7 天）是否随长期灰度数据调整待定。
2. 出网网关的黑词误拦截（用户正常提到"密码学"）如何平衡？当前策略：命中即拦截+澄清话术，灰度观察误拦率再调。

## 9. 变更记录

| 日期 | 版本 | 变更 | 依据 |
|---|---|---|---|
| 2026-08 | v4.1.0 | 自 ROADMAP §4.M0 迁出成书；并入出网网关/叹气三阶段/进程黑名单/RETURNING 三源 | 六轮研讨 |
| 2026-08 | v4.1.1 | 新增 §3.0 跨模块事件总线（backend/event_bus.py，已实现）；按代码现状校准 status/配置文件名/网关依赖；修正「全模块解耦唯一通道」表述 | 代码现状对齐；讨论定稿 |
| 2026-08 | v4.1.2 | 实现宏观四态状态机（backend/macro_state.py，T5）：ACTIVE/IDLE/DORMANT/RETURNING + `macro.state_changed` 事件 + 决策 4.3（RETURNING 仅用户主动对话触发）/4.5（DORMANT 情感衰减暂停钩子）+ RETURNING 分层问候；先写 test_macro_state.py 锁三纪律（22 项全绿） | T5 落地；MVP 四条硬基线之宏观四态闭合 |
