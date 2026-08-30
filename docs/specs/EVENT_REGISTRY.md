# 事件总线全局注册表（Event Registry）

> **唯一事实源**：事件总线解耦是全系统接口契约（见 `TEMPLATE.md` §6 与 `M0-core.md` §3.0）。**所有跨模块事件必须在本文件登记**；未登记的事件上线视为架构违例。payload 统一以**发布者**版本为准（模块书间若不一致，先改本表，再同步发布者）。
> 更新日期：2026-08-29 ｜ 状态：**集中登记 v1**（26 条事件 + 2 条命令通道）
> 全局询问预算与优先级仲裁见文末 §5。
>
> **实现状态（v4.1.1）**：跨模块语义化事件总线已落地 `backend/event_bus.py`，本表是其唯一事实源；事件名清单已同步进该文件 `EVENT_TYPES` 白名单。**新增事件 = 本表登记 + `EVENT_TYPES` 白名单同步补入**，缺一不可（发布端写错名会 fail-fast，不静默断链）。与 `backend/session/state.py`（前端会话状态事件流）解耦，二者不混用。

## 一、事件总表（按事件名）

| 事件名 | payload | 发布者 | 订阅者 | 出处 | 备注 |
|---|---|---|---|---|---|
| `affect.updated` | `{mood,intimacy,原因事件}` | M2 | 前端/M3 | M2 §6 | 星云渲染+主动参考 |
| `attention.fullscreen` | `{on/off, 进程名}` | M0 | M3 | M0 §6 | 主动候选挂起（payload 以 M0 为准） |
| `attention.sigh` | `{置信, 键鼠活跃}` | M0 | M2 | M0 §6 | **仅星云形态**；不进姿态判定池（v4.1.1） |
| `device.command` | `{target,action,approval_level}` | M5 | 执行器 | M5 §6 | 经授权中心分级 |
| `device.state_changed` | `{entity,前后态}` | M5/M0.3 | M1/M3 | M5 §6 | HA 推送驱动 |
| `env.anomaly` | `{传感器,数值}` | HA 推送 | M3/M2 | M5 §6 | 紧急穿透白名单判定 |
| `gateway.blocked` | `{词类, 处置}` | M0.2 | 日志 | M0 §6 | 高危词统计（不出网） |
| `gateway.entities_found` | `[疑似人名]` | M1.2 巩固 | M0.2/M1.5 | M0 §6 | 实体回收闭环入口 |
| `gateway.obfuscate` / `gateway.restore` | `{text}` | M4 | M0.2 | M4 §6 | 帧提取文本出网前过网关 |
| `growth.candidate` | `{事件,能力凭证}` | 任务层/M5 | M6 | M6 §6 | 双源制候选 |
| `growth.canonized` | `{记录id,轨别}` | M6 | M1 | M6 §6 | 入册后转正式记忆 |
| `macro.state_changed` | `{前态,后态,时长}` | M0 | M3/M2/M6 | M0 §6 | DORMANT 冻结（payload 以 M0 为准） |
| `memory.clarify_request` | `{条目id,二选一项}` | M1 | 会话层 | M1 §6 | 消耗周配额（→全局询问预算 §5） |
| `memory.export_requested` | `{范围}` | 用户 | M6/M1 | M6 §6 | 打包流程 |
| `memory.profile_updated` | `{版本,变更字段}` | M1 | M2 | M1 §6 | 姿态缓存刷新快照式（payload 以 M1 为准） |
| `micro_request.asked` | `{类型,用户响应}` | M6 | M1 | M6 §6 | 请求本身也入画像 |
| `plan.landed` | `{方案,落地凭证}` | M5 | M1/M6 | M5 §6 | 复盘与成长记录素材 |
| `posture.changed` | `{前态,后态,触发信号}` | M2 | M1/前端 | M2 §6 | 巩固策略调整+星云联动（payload 以 M2 为准） |
| `proactive.candidate` | `{类型,四维分,内容草案}` | M3 内部 | M3 仲裁器 | M3 §6 | 预算消费前 |
| `proactive.delivered` | `{id,用户响应}` | M3 | M1/M3 画像 | M3 §6 | 三态反馈入记忆 |
| `schedule.anniversary` | `{类型,事件}` | M1/M3 | M3 | M3 §6 | 穿透豁免判定 |
| `shadow.posture_decision` | `{会话id,决策,信号}` | M2 | 影子日志 | M2 §6 | 真路由上线前 ≥1 周 |
| `user.feedback` | `{目标,三态,原因}` | M0 偏好引擎 | M1 | M1 §6 | 转为 behavior 条目 |
| `vision.conclusion` | `{场景, 文字结论}` | M4 | M1 | M4 §6 | 入记忆（唯一持久化） |
| `vision.feedback` | `{三态}` | M4 | M0 偏好引擎 | M4 §6 | 反馈闭环 |
| `vision.session_state` | `{session_id, state}` | M4 | 前端/星云 | M4 §6 | 视线对齐渲染 |

## 二、命令通道（非总线事件，登记备案）

| 通道 | payload | 调用方 | 接收方 | 备注 |
|---|---|---|---|---|
| `tool.remember` / `tool.forget` | `{条目}` | 会话层 | M1 | 显式记忆 API（保留现有，直调而非总线） |

## 三、使用规则

1. **新增事件先登记（四处同步）**：本表 + 发布者/订阅者两处模块书 §6 + `backend/event_bus.py` 的 `EVENT_TYPES` 白名单 —— 四处同步，缺一即评审失败（EVENT_TYPES 不同步 → 发布/订阅会 fail-fast）
2. **payload 冲突谁为准**：发布者为准；模块书间不一致以本表为裁决
3. **禁止未登记上线**：代码 review 时凡跨模块调用未在本表出现 → 架构违例拒绝合入
4. **跨模块禁止直调核心函数**：一律事件+只读快照（TEMPLATE §6 原则）——用 `backend/event_bus.py` 的 `bus.on/emit`，禁止跨模块 `import` 对方模块核心函数

## 四、模块输入/输出速查

（按模块看它输入什么、输出什么，快速定位依赖边界）

| 模块 | 发布（输出） | 订阅（输入） | 读取快照 |
|---|---|---|---|
| M0 | macro/attention/gateway.blocked | user.feedback（偏好引擎转交） | — |
| M1 | memory.profile_updated / clarify / gateway.entities_found | posture.changed / user.feedback / proactive.delivered / memory.export_requested | — |
| M2 | posture.changed / affect.updated / shadow | memory.profile_updated / attention.sigh | M1 画像快照 |
| M3 | proactive.* / schedule.anniversary | macro/attention / affect.updated / device.state_changed / env.anomaly | — |
| M4 | vision.* / gateway.obfuscate | — | M1 条目 |
| M5 | device.* / plan.landed / env.anomaly(转) | gateway.restore | — |
| M6 | growth.* / micro_request / memory.export_requested | growth.candidate / plan.landed | — |

## 五、全局周询问预算（v4.1.1）

**问题**：M0 §5 同轮单一询问解决了"**轮内**"推斥；但**周内**跨模块询问无仲裁——M1 澄清(2-3) + M6 册封(1) + M2 印证(1-2) 合计最多 ≈6 次/周，可能过扰。

**规则（写死）**：
- **全局上限：5 次/周**（`inquiry_budget.weekly_max`，config 可配）
- 各模块分项上限不叠加，而是**共享**全局 5 次配额，按优先级竞争消耗：
  - **优先级**：高危干预（M2 即时）> 记忆澄清（M1）> 关系类询问（M6 册封 / M2 邀约印证）
  - 消耗顺序：高危干预**不占配额**（即时性需保障）；记忆澄清<关系类，先到先得，冲突时高优先级顶替，低优先级顺延下周
- 与 M0 §5 轮内规则协同：轮内只一个；周内总量 ≤5；两规则同时生效
- **Starvation/Aging 防护（v4.1.1 终审）**：低优先级功能（M6 册封/M2 印证）不得被 M1 澄清长期饿死——候选连续 3 周被顺延则第 4 周全优先级提权强制消耗（见 M6 §4.1 Aging Policy）；规则本身不推翻，只做底线保护
- **各模块书配额行措辞统一改为**："本项询问**受全局周询问预算 ≤5 次/周约束**（见 EVENT_REGISTRY §5），此为模块分项上限，非独立预算"
