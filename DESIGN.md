# 小二（Xiao）· 语音工作助手 · 设计方案（v3）

> **v3 定版（架构 + 技术栈锁定）**：架构 = 混合 C 三层（Python 语音引擎 + DSH 薄插件 JS + Electron/React UI）；技术栈 = **除唤醒词本地（Sherpa-ONNX「小二」）外，ASR/LLM 一律 API 优先**，并支持**多方案可存可切**（ASR：阿里云实时流式 qwen-audio 默认 / fun-asr 方言备选 + 本地 FunASR；LLM：DeepSeek/千问/OpenAI/GLM/Kimi 云端 + Ollama/MiniCPM-o 本地）。开发路线图见 `ROADMAP.md`。本文档 v2.1 的详细设计（状态机/路由/桥/审批/风险）与定版不冲突的部分继续有效。

> 一句话定位：给通用 Agent（DeepSeek Harness，DSH）装上语音前端，做成「语音控制的 Agent 工作台」。
>
> 修订史：v2 新增「风险与应对」「语音审批」「长任务异步」三节；v2.1 吸收外部分析（DSH 版本更正 `0.1.1-rc.2`、审批栈源码核实 headless fail-closed、§9 补并发任务与失败/超时反馈）；v2.2 审批更新为**双层结构**——预测式语音审批 + 运行时审批桥 `xiao-approval-bridge`（已落地），A2 收窄为纯流式桥。

---

## 1. 定位与核心判断

**本质不是「语音助手」，而是「给通用 Agent 装语音前端」。** 传统语音助手在「自己造大脑」（意图引擎、技能系统、多轮对话）；本设计把最难的部分外包给 DSH（agent 循环 + 编程工具 + subagent + 知识库），自己只做三件事：

1. **语音链路**：唤醒 / 识别 / 断句 / 合成（已跑通，中文识别对标微信/豆包）
2. **路由**：一句话走「聊天」还是「干活(DSH)」
3. **薄桥接**：防腐层——桥的两端各一个模块（Python 侧 bridge/ + DSH 侧薄插件）

### 三条边界（工程核心）

| 边界 | 内容 |
|---|---|
| 进程边界 | 语音系统是独立进程，DSH 是被调用的外部服务，只经 CLI/API 通信，互不侵入 |
| 目录边界 | 本仓库（语音系统）/ `~/.dsh`(DSH 运行时) / DSH 插件开发目录 / Agent 工作区（`agent.workspace` 配置）互不重叠 |
| 耦合边界 | 桥的两端各一个模块知道 DSH：Python 侧 `backend/bridge/` + DSH 侧薄插件(JS)；DSH 升级只改这两处 |

## 2. 整体架构

```
[语音前端]                          [DSH 大脑]
 Sherpa-ONNX 唤醒「小二」             agent 循环
 阿里云流式 ASR(qwen-audio)/FunASR   编程工具（文件/终端）
 Silero VAD 断句         薄桥接      subagent / workflow
 TTS(Qwen流式/edge/Piper/omni) ─▶  知识库检索
 DeepSeek/千问(chat 通道)            DSH 薄插件(JS) 注入
 自定义工具(天气/提醒/打开/搜索)
```

- **语音前端**：已落到代码并跑通；中文识别对标微信/豆包。
- **大脑**：DSH（agent + 编程 + 多 Agent + 知识库）。
- **桥接**：`backend/bridge/`，全系统唯一知道 DSH 的模块。

## 3. 目录结构

```
Xiao\（本仓库 · 语音系统，独立可运行）
├── backend\
│   ├── audio\  mic/vad/wake
│   ├── asr\    阿里云实时流式(qwen-audio默认/fun-asr方言备选) / FunASR(本地)
│   ├── llm\    OpenAI兼容(DeepSeek/通义/OpenAI/GLM/Kimi/Ollama/MiniCPM-o)
│   ├── tts\    Qwen 实时流式(默认) + edge-tts(免费云) + Piper(离线) + MiniCPM-o(vLLM)
│   ├── tools\  搜索/打开/天气/提醒
│   ├── bridge\ ★ 唯一知道 DSH 的地方
│   ├── session\ 状态机 + 事件总线
│   ├── router.py 路由层
│   ├── agent.py core.py main.py
├── frontend\  React 工作台
├── desktop\   Electron 壳（可选）
├── plugins\   DSH 薄插件（审批桥 xiao-approval-bridge）
└── config.yaml / .env.example / run.py

~/.dsh\              ← DSH 运行时（DSH 自建，独立于本仓库）
<agent.workspace>    ← Agent 工作目录（DSH 读写文件处，运行时自动创建；config.yaml 可配，默认相对项目根）
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
- `AWAIT_APPROVAL`：危险工具审批态，见 §8（双层审批已落地）。

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
- **L0 规则层（B1，优先于路由）**：`backend/rules.py` 在路由之前先匹配触发词——音量/静音、截图、锁屏/睡眠、播放/暂停/上下曲、剪贴板复制/粘贴/朗读、查时间、查天气、查汇率、定时提醒、打开应用/网址——命中直接执行内置工具并播报，**无 LLM、无 key 也能用**；提取不到必要参数（如「提醒我」没说多久、单说「打开」）自动回落对话/DSH 不硬拦截。词表在 `config.yaml` 的 `router.rules.keywords`（可按规则覆盖默认口令，`router.rules.enabled: false` 一键关）。锁屏/睡眠为「先说完再做」型，避免动作打断播报。

设计意图：解决「闲聊要快、干活要慢」的语义错配——agent 任务动辄几十秒到几分钟，不能每句都走 DSH。

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
- **A2（规划，优先级高于工作面板）**：耦合 DSH 的 WS/RPC（即常驻 `dsh web` + 连其回环 WS/HTTP，而非 headless CLI），实现流式 + 连续会话 + 工具事件回传（审批注入已由运行时审批桥落地，见 §8，不再依赖 A2）。
  - 代价：DSH 处于 rc 阶段（实测 `0.1.1-rc.2`），耦合越深、版本一变越痛。**先 A1 稳、后 A2 快，是权衡而非偷懒。**
- **版本风险应对**：锁版本 + 单一适配层（`bridge/`）+ 优先走稳定 CLI 面；DSH 变动只改一个文件。

## 8. 语音审批（安全）

DSH 的危险操作审批由 **`dsh-user-approval`** 服务承担（服务名 `ctx.approval`；不是 `dsh-authorization`，那是凭据/OAuth 授权）。缺口「审批如何回到语音」**已由运行时审批桥闭合**（v2.2 落地，见下）。以下结论已读 DSH 源码核实。

- **A1 阶段（已核实：fail-closed 安全默认）**：`dsh --profile headless` 默认审批策略 `ask`，但 headless 不挂 Web/应答者，审批请求解析为 `unavailable` → 工具层映射 `deny`（"no approval channel is available"）。即**危险操作在无头模式下默认拒绝，且无外部注入点**。语音侧告知“这个操作被无头模式安全拦截了”，不静默执行。
  - ⚠ 别被 `DSH_PERMISSION_MODE=danger-full-access` 的名字骗了：它把审批策略切成 `never`，语义是**确定性拒绝**（源码 `NEVER_SENTENCE`：“actions that require approval are rejected automatically”），不是放行。
- **第一层·预测式语音审批（已落地）**：状态机已有 `AWAIT_APPROVAL`；DSH 任务执行前，`core.py` 用权限关键词预测「需要哪些权限」，语音播报询问，用户可说「允许/拒绝」或点屏幕按钮（`answer_approval` 回填 future）。
  > ⚠ **预测式的局限（须知情）**：关键词预测会**误报**（多问无关权限）和**漏报**（没命中关键词的高危动作不拦截）。尤其对**「调软件」类**（点鼠标/键盘、发消息、删数据）这种高危动作，漏报风险会被放大，**不能只靠预测式兜底**——届时需优先走 UIA/COM 这类可控接口，并对危险动作单独从严。DSH 内部真实触发的审批已由第二层精确接住。
- **第二层·运行时审批桥（v2.2 已落地；源码核实 `plugins/xiao-approval-bridge/` + `backend/main.py`）**：headless 缺的「应答者」由 DSH Host-only 插件补上——插件在 `approval/request` 瀑布里注册 answerer，把 DSH 内部**真实审批请求**引回语音链路：
  1. DSH 工具触发审批（触发面 = `bash`/`pwsh` 沙箱升权重试、`write`/`edit` 写工作区外；网络访问**不触发** DSH 审批）；
  2. 插件先查 `XIAO_GRANT` 环境变量预授权（`BUCKET_OF_TOOL`：bash/pwsh→`command`，write/edit→`write_outside`），命中直接放行；
  3. 否则回环 `POST http://127.0.0.1:8123/api/dsh/approval`（仅 127.0.0.1/::1，否则 403）→ `core.request_approval()` 进 `AWAIT_APPROVAL`，语音 + 屏幕按钮双通道询问；
  4. 决策回填：`allowed-once`（一次性放行）/ `rejected` / `unavailable`（fail-closed）。
  - 这一层是**精确审批**：DSH 内部真触发了才问，不靠关键词预测、无误报；A1 的 fail-closed 结论对「不装本插件的原生 headless」依然成立（裸 headless 无 answerer → 默认拒绝）。
- **A2 阶段（流式桥，规划；v2.2 收窄）**：审批注入**已由运行时审批桥实现**，A2 剩余价值 = `dsh web` 的流式输出 / 连续会话（免每轮冷启动、重发历史）/ 工具事件回传（实时进度而非等终态）。web 模式的审批通道（回环 WS `/api/events.mux` 订阅 `approval/requested` 帧，经 `POST /api/respond` 注入 `{rpcId, approvalId, outcome:'allowed-once'|'rejected'}`；无认证、仅回环可达、只有一次性授权）作为插件桥的**备选路径**保留记录，不再阻塞路线图。
  - 代价：A2 从「薄桥 CLI」变成「常驻 DSH web + 语音↔HTTP 桥」，安全边界靠回环；DSH 仍在 rc 阶段，耦合越深、版本一变越痛（权衡取舍见 §7）。

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
| 1 | ASR/TTS 非全离线（edge-tts 微软云、阿里云 ASR） | 属权衡非错误；本地 FunASR 已在 `config.yaml` 可切（重但存在）。若主打隐私需改口径 |
| 2 | A1 CLI 桥薄弱（stateless/非流式/冷启动/重发历史） | 提到最高优先级做 A2；锁版本 + 适配层隔离 |
| 3 | 语音触发的高危操作无审批 | §8：A1 默认拦截，A2 做 `AWAIT_APPROVAL` 交互审批 |
| 4 | 长任务的心智成本 | §9：后台化 + 完成通知，不强制干等 |
| 5 | DSH 执行失败/超时无语音反馈 | 状态机加 `WORKING --失败/超时--> SPEAKING(报原因)`；§9 已列 |

## 11. 关键配置

```yaml
agent.workspace     → ../03_Workspace（相对项目根；DSH 工作区）
agent.system_prompt / agent.max_history  → 系统提示词 / 记忆轮数（软配置，保存即生效）
bridge.dsh_command  → dsh（.ps1 自动用 powershell 拉起）
bridge.timeout_sec  → 600
router.mode         → auto | chat | dsh
router.log_path     → logs/routes.jsonl
router.dsh_keywords / working_status_phrases / working_cancel_phrases
router.rules        → L0 规则指令开关与词表（keywords 按规则覆盖，B1）

# 多方案管理（四环节）：models[] 存方案，active 指向当前方案
wake_word.engine    → sherpa（本地）| cloud（方言·预留）| omni（一体化·预留）
asr.provider        → cloud | local(FunASR) | omni（预留）
asr.cloud.model     → qwen-audio-3.0-asr-flash-streaming（普通话·默认）/ fun-asr-flash-8k-realtime（方言备选·8k）
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
- **配套接口**：`/api/audio/devices`（sounddevice 枚举）、`/api/tts/preview`（试听）、`/api/memory/clear`（一键清空 Agent 历史 + DSH 上下文）、`/api/provider/test`（服务商连通性测试：按环节发最小请求，无效 Key/超额/超时各回一句人话，不抛堆栈）、`/api/health/probe`（健康状态灯：并行探测 ASR/LLM/TTS 当前激活方案 + 检查本机 dsh 命令，返回各环节绿/红 + 延迟 + 一句人话原因）。
- **统一报错映射**：`backend/errors.py`（`human_reason` / `reason_from_text`）——管线任何环节的异常（对话、长任务、试听、设备枚举）都转成一句可播报的人话：401 = Key 失效、429 = 额度/限流、超时 = 网络；原始错误只进后端日志与前端日志面板，不抛堆栈给用户。
- **首次启动向导**（`OnboardingWizard.tsx`，复用设置面板样式）：选语言 → 领 Key（DeepSeek / 通义百炼直达领取页 + 图文步骤）→ 连通测试（`/api/provider/test`，✅/❌ 含人话原因）→ 选大脑（`router.mode` 三选一 + DSH 可用性检测）→ 测麦克风（`/api/mic/echo`）。任何一步可跳过进 L0；「已完成」标记存本机 localStorage（`xiao_onboarded`），保存失败不写标记、下次仍会弹。
- **LLM 高级参数**（新增/编辑模型弹窗内）：模型名自填 + 引导（如「填 deepseek-chat / qwen-plus」，不内置下拉）；上下文窗口（输入/输出）、工具调用轮数（默认 500）、思考模式、图片输入、采样 Top P / Top K。`top_p` / `max_tokens` 透传各家，`top_k` 仅对兼容 extra_body 的供应商透传（DeepSeek/OpenAI/Kimi 仅保存不发送，避免 400）；弹窗内置「连通测试」按钮并提示「连通性测试会消耗少量 Token」。Agent 侧配套**有界多轮工具循环**：按「工具调用轮数」反复执行-回传-续推，超限时强制模型文本收尾，不再一轮就停。

## 12. 与开源方案对比（横向定位）

与 Leon / OVOS / Rhasspy / Home Assistant / Khoj 等开源方案的逐项对比表已从对外文档移除，避免双份维护与过时风险。

**一句话定位**：本设计填补真实空档——把「语音」接到一个能改文件、跑命令、调子 Agent 的通用 Agent 上。

## 13. 路线图（定版）

路线图已移至 `ROADMAP.md`（8 阶段，云端 API 优先），此处不再重复，避免双份维护。

## 14. 开源

已开源到 GitHub（`glaincer-lab/Xiao`，MIT）。整体方案见 `OPEN_SOURCE.md`（素材版权已确认无风险）。

## 15. 工程约定与注意事项

> 原 `PROGRESS.md` 的本节已并入此处。这是维护与二次开发时常踩的坑，务必遵守。

1. **密钥**：真实 key 只在本机 `.env`（已 gitignore），`config.yaml` 一律留空，仓库只留 `.env.example`（全占位符）。
2. **Git**：schannel SSL 失败，用 `git -c http.sslBackend=openssl`；push 用 gh token 拼 URL（`gh auth token` + `x-access-token:<tok>@github.com`）。
3. **Python 环境**：用 `.venv\Scripts\python.exe`（3.12，venv 无 pip，用 `uv` 装包，需设 `UV_CACHE_DIR` 到临时目录）。**勿用 3.14**（torch/FunASR 无 wheel）。若本机只有 uv 管理的 3.12，用其完整解释器路径建 venv（`py -3.12` 不一定可用）。
4. **项目规则**：设计以「第三使用者视角」考虑（可分发、开箱即用、不写死本机路径），见 `AGENTS.md`。
5. **音色命名坑**：三套音色命名互不通用——CosyVoice 短名（`longanyang`）、Qwen-Audio-TTS 带模型前缀（`qwen-audio-3.0-tts-flash-longxxx`）、Qwen 实时流式英文名（Ethan 等）；存储层统一存短名、运行时拼接。
6. **重启生效**：引擎类配置改动（`reload: restart`）需重启后端；`reload: soft` 保存即热加载。
7. **云 TTS 语速边界（已核）**：仅 edge-tts 有 `rate` 语速（±50%，设置里可选）；CosyVoice v3 / Qwen-Audio-TTS 底层 `SpeechSynthesizer` 支持 `speech_rate`（0.5~2.0，<1 放慢、>1 加快）但**当前代码未透传**；Qwen 实时流式（`qwen_rt`）官方明确**不支持** `speech_rate`（Qwen-TTS-Realtime 系列忽略该字段），原生调速走不通，仅可换 `qwen3-tts-instruct-flash-realtime` 用指令近似控制。
8. **设计文档位置（勿丢失）**：设置系统与 UI 设计记录已移至 `dev/settings-design.md`（**gitignore，不推送**）。该文件含方案①②③、字段注册表、UI 对比、实施顺序与待确认项，是「设置 + 界面」后续迭代的设计依据。核心待办：健康检查+自动降级（⬜）、星云 Canvas 2D 重做（⬜）、快速抽屉/全屏高级页细化（⬜）。下次继续设置/UI 工作时**一定先读此文件**。
9. **测试门禁**：后端 `python -m unittest discover -s tests`；前端 `npm run check`（= typecheck + lint，ESLint v9 扁平配置 `frontend/eslint.config.js`）+ `npm run build`。模型/配置类一致性回归集中在 `tests/`（如 LLM 工厂 omni 路径、设置 schema 选项合法性）。
