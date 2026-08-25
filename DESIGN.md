# 小二（Xiao）· 语音工作助手 · 设计方案（v3）

> **v3 定版（架构 + 技术栈锁定）**：架构 = 混合 C 三层（Python 语音引擎 + DSH 薄插件 JS + Electron/React UI）；技术栈 = **除唤醒词本地（Sherpa-ONNX「小二」）外，ASR/LLM 一律 API 优先**，并支持**多方案可存可切**（ASR：Paraformer 云端普通话/方言 + 本地 FunASR；LLM：DeepSeek/千问/OpenAI/GLM/Kimi 云端 + Ollama/MiniCPM-o 本地）。开发路线图见 `ROADMAP.md`。本文档 v2.1 的详细设计（状态机/路由/桥/审批/风险）与定版不冲突的部分继续有效。

> 一句话定位：给通用 Agent（DeepSeek Harness，DSH）装上语音前端，做成「语音控制的 Agent 工作台」。
>
> v2 变更：新增「风险与应对」「语音审批」「长任务异步」三节；路线图把 **A2 流式桥**提到「工作面板 UI」之前。
>
> v2.1 修订（吸收外部分析）：DSH 版本更正为 `0.1.1-rc.2`；§8 补 DSH 审批栈源码核实结论（headless fail-closed；A2 走 web 模式注入）；§9 补并发任务列表与失败/超时反馈；§10 增风险 5；§12 对比表拆分并软化表述；§13 增「失败回退」「历史摘要压缩」两项。

---

## 1. 定位与核心判断

**本质不是「语音助手」，而是「给通用 Agent 装语音前端」。** 传统语音助手在“自己造大脑”（意图引擎、技能系统、多轮对话）；本设计把最难的部分外包给 DSH（agent 循环 + 编程工具 + subagent + 知识库），自己只做三件事：

1. **语音链路**：唤醒 / 识别 / 断句 / 合成（已跑通，中文识别对标微信/豆包）
2. **路由**：一句话走「聊天」还是「干活(DSH)」
3. **薄桥接**：防腐层——桥的两端各一个模块（Python 侧 bridge/ + DSH 侧薄插件）

### 三条边界（工程核心）

| 边界 | 内容 |
|---|---|
| 进程边界 | 语音系统是独立进程，DSH 是被调用的外部服务，只经 CLI/API 通信，互不侵入 |
| 目录边界 | `02_Assistant`(语音系统) / `~/.dsh`(DSH 运行时) / `01_DSH`(插件开发) / `03_Workspace`(工作区) 互不重叠 |
| 耦合边界 | 桥的两端各一个模块知道 DSH：Python 侧 `backend/bridge/` + DSH 侧薄插件(JS)；DSH 升级只改这两处 |

## 2. 整体架构

```
[语音前端]                          [DSH 大脑]
 Sherpa-ONNX 唤醒「小二」             agent 循环
 Paraformer 流式 ASR / FunASR 本地   编程工具（文件/终端）
 Silero VAD 断句         薄桥接      subagent / workflow
 edge-tts TTS         ───────────▶  知识库检索
 DeepSeek/千问(chat 通道)            DSH 薄插件(JS) 注入
 自定义工具(天气/提醒/打开/搜索)
```

- **语音前端**：已落到代码并跑通；中文识别对标微信/豆包。
- **大脑**：DSH（agent + 编程 + 多 Agent + 知识库）。
- **桥接**：`backend/bridge/`，全系统唯一知道 DSH 的模块。

## 3. 目录结构

```
005_Agent\
├── 01_DSH\            ← DSH 插件开发（不动）
├── 02_Assistant\      ← ★ 语音系统（一套系统一个文件夹，独立可运行）
│   ├── backend\
│   │   ├── audio\  mic/vad/wake
│   │   ├── asr\    Paraformer(云端·普通话/方言) / FunASR(本地)
│   │   ├── llm\    OpenAI兼容(DeepSeek/通义/OpenAI/GLM/Kimi/Ollama/MiniCPM-o)
│   │   ├── tts\    edge-tts(免费云) + Piper/付费云(预留)
│   │   ├── tools\  搜索/打开/天气/提醒
│   │   ├── bridge\ ★ 唯一知道 DSH 的地方
│   │   ├── session\ 状态机 + 事件总线
│   │   ├── router.py 路由层
│   │   ├── agent.py core.py main.py
│   ├── frontend\  React 工作台
│   ├── desktop\   Electron 壳（可选）
│   ├── plugins\   DSH 薄插件（审批桥 xiao-approval-bridge）
│   └── config.yaml / .env / run.py
└── 03_Workspace\      ← ★ Agent 工作目录（DSH 读写文件处，运行时自动创建）
```

## 4. 状态机

```
IDLE/SLEEPING ──唤醒──► LISTENING ──路由──► chat: PROCESSING/EXECUTING/SPEAKING
                                                    │
                                                    └─► dsh: WORKING（长任务，可问进展/取消）
LISTENING ──静音超时──► SLEEPING
LISTENING ──说「关闭」──► CONFIRM_SHUTDOWN（两步确认）──► 退出
```

- `WORKING`：DSH 任务执行中，可问「进展」报已用秒数、说「取消」停止；**支持后台化**（见 §9）。
- `CONFIRM_SHUTDOWN`：关闭前二次确认，5 秒不答自动取消。
- `AWAIT_APPROVAL`（**规划**）：危险工具审批态，见 §8。

## 5. 路由（A1b 双通道）

一句话走「聊天」还是「干活(DSH)」，由 `backend/router.py` 决定：

| 模式 | 行为 |
|---|---|
| `auto` | 关键词命中 → DSH；否则 → 聊天 |
| `chat` | 强制聊天（DeepSeek 直连 + 自定义工具） |
| `dsh` | 强制走 DSH |

- 关键词在 `config.yaml` 的 `router.dsh_keywords`（初期保守，按日志调优）。
- **路由日志** `logs/routes.jsonl`：每次决策写一行 JSON（时间/模式/判定/原文），可度量、可调优。
- **手动强制**：前端顶栏「自动/聊天/DSH」三档开关，实时下发 `router_mode`。

设计意图：解决“闲聊要快、干活要慢”的语义错配——agent 任务动辄几十秒到几分钟，不能每句都走 DSH。

## 6. 语音控制指令

| 指令 | 行为 | 确认 |
|---|---|---|
| 你退下吧 / 你可以回去了… | 回待机 + 清空历史 | 否 |
| 清空对话 / 重新开始… | 清空历史，留聆听态 | 否 |
| 你把自己关了吧 / 退出程序… | 两步确认 → 退出整个后端 | 是 |

## 7. DSH 桥接（A1 已落地 · A2 规划）

- **A1（已落地并验证）**：`dsh --profile headless "任务"`，走最稳定的 CLI 面。headless 无状态，`backend/bridge/dsh_bridge.py` **自己维护多轮上下文**——记录最近 N 轮任务与结果摘要（`_context_max=6`，结果摘要截断 500 字），每轮把「历史 + 当前任务」打包成一条 prompt 发给 DSH，绕开 headless 的 stateless。
  - 清空历史 / 退下 / 设置面板「一键清空记忆」都会同步清 DSH 上下文（`reset_context()`）。
  - 代价：非流式、进程冷启动、每轮重发历史 token。
- **A2（规划，优先级高于工作面板）**：耦合 DSH 的 WS/RPC（即常驻 `dsh web` + 连其回环 WS/HTTP，而非 headless CLI），实现流式 + 连续会话 + 工具事件回传 + 审批注入（见 §8）。
  - 代价：DSH 处于 rc 阶段（实测 `0.1.1-rc.2（读自本机发行版 package.json）`），耦合越深、版本一变越痛。**先 A1 稳、后 A2 快，是权衡而非偷懒。**
- **版本风险应对**：锁版本 + 单一适配层（`bridge/`）+ 优先走稳定 CLI 面；DSH 变动只改一个文件。

## 8. 语音审批（安全）

DSH 的危险操作审批由 **`dsh-user-approval`** 服务承担（服务名 `ctx.approval`；不是 `dsh-authorization`，那是凭据/OAuth 授权）。缺口是**审批如何回到语音**。以下结论已读 DSH 源码核实。

- **A1 阶段（已核实：fail-closed 安全默认）**：`dsh --profile headless` 默认审批策略 `ask`，但 headless 不挂 Web/应答者，审批请求解析为 `unavailable` → 工具层映射 `deny`（"no approval channel is available"）。即**危险操作在无头模式下默认拒绝，且无外部注入点**。语音侧告知“这个操作被无头模式安全拦截了”，不静默执行。
  - ⚠ 别被 `DSH_PERMISSION_MODE=danger-full-access` 的名字骗了：它把审批策略切成 `never`，语义是**确定性拒绝**（源码 `NEVER_SENTENCE`：“actions that require approval are rejected automatically”），不是放行。
- **语音审批（已落地）**：状态机已有 `AWAIT_APPROVAL`；DSH 任务执行前，`core.py` 用权限关键词预测「需要哪些权限」，语音播报询问，用户可说「允许/拒绝」或点屏幕按钮（`answer_approval` 回填 future）。A1 阶段 DSH 自身审批在无头模式下 fail-closed（默认拒绝，见下），语音侧不静默执行。
  > ⚠ **预测式审批的局限（须知情）**：当前语音审批是**「预测式」**——`core.py` 在 DSH 执行**前**用权限关键词猜「这次要哪些权限」再问。这是**近似**，不是 DSH 内部的精确审批（那要等 A2 走 web 模式注入）。关键词预测会**误报**（多问无关权限）和**漏报**（没命中关键词的高危动作不拦截）。尤其对**「调软件」类**（点鼠标/键盘、发消息、删数据）这种高危动作，漏报风险会被放大，**不能只靠预测式审批兜底**——届时需优先走 UIA/COM 这类可控接口，并对危险动作单独从严。
- **A2 阶段（交互式审批，规划；路径已核实可行）**：审批句柄存在（`approvalId` 审计 UUID + `rpcId` 应答句柄），但**只在 Web 模式对外暴露**，headless 拿不到。可行路径：
  1. 把桥从 headless CLI 换成 **`dsh web`**，语音后端连它回环 WebSocket `/api/events.mux` 订阅 `approval/requested` 帧（含 `rpcId`/`approvalId`/`toolName`/`reason`）
  2. 进入 `AWAIT_APPROVAL`，语音问“要执行「…」吗？”
  3. 决策经 **`POST /api/respond`** 注入：`{rpcId, result:{ok:true, value:{sessionId, approvalId, outcome:'allowed-once'|'rejected'}}}`
  - 约束：无认证（仅回环/trustedHosts 可达）；无“列出待审批”接口（只能订阅推送流拿句柄）；只有一次性授权 `allowed-once`（无“始终允许”）。
  - 代价：A2 从“薄桥 CLI”变成“常驻 DSH web + 语音↔HTTP 桥”，安全边界靠回环。

## 9. 长任务与异步交互

长任务 + 语音天然冲突（人盯着语音等结果很累）。设计为**混合模式**：

- **留在 WORKING**：可问「进展」（报已用秒数）、说「取消」。
- **后台化**：说「后台跑 / 你先忙」→ 助手回“好的，我后台继续，完成后叫你”→ 回到待机，任务继续；**完成后语音主动通知**“任务完成了：…”。
- 两段式回复（先说准备做什么 → 完成再汇报结果）不变。
- **后台并发**：后台化会同时存在多个 DSH 任务，状态机需把单一 `WORKING` 升级为**任务列表**（每个带 id/状态/完成事件），“取消”要能消歧义指向哪个任务。
- **失败/超时反馈**：`WORKING --失败/超时--> SPEAKING(报原因) --> SLEEPING`，不能让任务卡在 WORKING 里一声不吭。

## 10. 风险与应对

| # | 风险 | 应对 |
|---|---|---|
| 1 | ASR/TTS 非全离线（edge-tts 微软云、Paraformer 阿里云） | 属权衡非错误；本地 FunASR 已在 `config.yaml` 可切（重但存在）。若主打隐私需改口径 |
| 2 | A1 CLI 桥薄弱（stateless/非流式/冷启动/重发历史） | 提到最高优先级做 A2；锁版本 + 适配层隔离 |
| 3 | 语音触发的高危操作无审批 | §8：A1 默认拦截，A2 做 `AWAIT_APPROVAL` 交互审批 |
| 4 | 长任务的心智成本 | §9：后台化 + 完成通知，不强制干等 |
| 5 | DSH 执行失败/超时无语音反馈 | 状态机加 `WORKING --失败/超时--> SPEAKING(报原因)`；§9 已列 |

## 11. 关键配置

```yaml
agent.workspace     → D:\04_Work\04_Project\005_Agent\03_Workspace
agent.system_prompt / agent.max_history  → 系统提示词 / 记忆轮数（软配置，保存即生效）
bridge.dsh_command  → dsh（.ps1 自动用 powershell 拉起）
bridge.timeout_sec  → 600
router.mode         → auto | chat | dsh
router.log_path     → logs/routes.jsonl
router.dsh_keywords / working_status_phrases / working_cancel_phrases

# 多方案管理（四环节）：models[] 存方案，active 指向当前方案
wake_word.engine    → sherpa（本地）| cloud（方言·预留）| omni（一体化·预留）
asr.provider        → cloud | local(FunASR) | omni（预留）
asr.cloud.model     → paraformer-realtime-v2（普通话）/ fun-asr-flash-8k-realtime（方言·8k）
llm.provider        → cloud | local(Ollama) | omni(MiniCPM-o vLLM)
llm.cloud.provider  → deepseek / dashscope / openai / glm / kimi（全 OpenAI 兼容）
tts.provider        → edge（免费云）| cloud（付费·预留）| piper（预留）| omni（预留）
# 每环节另有 <环节>.active + <环节>.models[]；API Key 存 <环节>.cloud.api_key，留空回退环境变量
```

## 11b. 设置系统（schema 驱动，已落地）

设置界面采用**字段注册表**驱动：`backend/settings_schema.py` 定义字段（`path`/`type`/`options`/`reload`/`hint`/`show_if`/`guide`），前端 `SettingsPanel.tsx` 拉 `/api/config/schema` 自动渲染，后端 `/api/config` 统一校验存储。新增选项只改注册表一行，不改前端。

- **类型**：`checkbox` / `select` / `slider` / `number` / `text` / `textarea` / `multiselect` / `guide`。
- **分组**：唤醒 / 识别 / 播报 / 大模型 / 执行 / 权限 + 音频（麦克风枚举）/ 界面（前端本地设置）；左侧导航 + 右侧滚动布局。
- **多方案管理（四环节）**：唤醒、识别、播报、大模型各自维护 `models[] + active`——界面上是「方案卡片列表 + 新建/编辑独立弹窗 + 可调属性（音色/语速/随机度/灵敏度）」；`[设为当前]` 切换 active、`[编辑]` 预填回填（Key 显示圆点）、`[删除]` 移除。未接入引擎标 ⬜ 预留（`status: planned`），选中显示引导块。
- **实时生效分档**（字段的 `reload` 属性）：
  - `soft`：保存即热加载（`core.reload_soft()`）——可打断、审批词表、DSH 关键词、系统提示词、记忆轮数、路由模式等。
  - `restart`：引擎类，保存后提示需重启后端——换 ASR/LLM/TTS 方案/模型/音色、唤醒词/阈值、麦克风设备等。
- **配套接口**：`/api/audio/devices`（sounddevice 枚举）、`/api/tts/preview`（试听）、`/api/memory/clear`（一键清空 Agent 历史 + DSH 上下文）。

## 12. 与开源方案对比（横向定位）

| 维度 | 本设计 | Leon | OVOS | Rhasspy | Home Assistant | Khoj | Krisduo |
|---|---|---|---|---|---|---|---|
| 定位 | **语音控制 Agent 工作台** | 桌面技能助手 | 语音技能中枢 | 全离线语音 | 家居自动化中枢 | 文档问答 | Mac 桌面管家 |
| 大脑 | **外包给 DSH**(agent+编程+多Agent+知识库) | 规则意图/技能 | 规则意图/技能 | 规则意图 | LLM 管线(可选) | RAG 检索 | LLM+function calling |
| 自主改文件/跑命令 | ✅ 核心能力 | ❌(仅预定义脚本△) | ❌ | ❌ | △(shell_command/脚本) | ❌ | 部分 |
| 多 Agent | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 中文 | ✅ 一等公民(Paraformer+DeepSeek) | 一般 | 一般 | 一般 | 一般 | 一般 | 一等公民 |

**结论**：本设计填补真实空档——把「语音」接到一个能改文件、跑命令、调子 Agent 的通用 Agent 上。

## 13. 路线图（定版）

路线图已移至 `ROADMAP.md`（8 阶段，云端 API 优先），此处不再重复，避免双份维护。

## 14. 开源（规划）

后期开源的整体方案见 `OPEN_SOURCE.md`。已定：**许可证 MIT**、**项目名 Xiao（小二）**。开源前待办：轮换密钥（暂缓，正式开源前执行）、Picovoice 残留（已清）、素材版权核查。
