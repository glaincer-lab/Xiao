# 隐私合规与授权改造方案（设计书）

> 版本 v1.0 ｜ 2026-09 ｜ 状态：已与老板逐条确认，待实施
> 背景：全项目隐私审计后，发现「默认最大化收集」（语音/对话/截屏/剪贴板默认上云或明文落盘，且几乎无用户同意闸门），
> 违反《个人信息保护法》第六条「最小必要」、第十三条/十四条「同意」、第四十七条「删除权」，
> 以及 GDPR 第 5 条「数据最小化」、第 25 条「默认保护」。
> 本方案把「隐私能力」统一收口为「授权项」（默认关 = 最小化），并重做开箱引导，让用户在知情下逐项授权。

---

## 一、核心原则

1. **默认最小化**：所有上云/敏感能力，开箱默认**关**，本人授权才开。
2. **本人授权**：每项开启前，弹「提示词 + 隐私说明」（以链接形式点击弹出），确认即同意。
3. **本地优先可选**：语音/对话/图片/合成，每项可选「云端」或「本地」。
4. **白名单存在且即生效**：紧急白名单单一事实来源，改完立即生效。
5. **可部分删除**：按时间区间 / 类型删除，不是只有全清空。
6. **本地存储、本人处理**：本地自用数据不强缩期限（保留 300MB 容量滚动），但必须补删除入口。

---

## 二、授权项设计（backend/authorization.py）

授权项从现有 5 项扩到 **12 项**。除「网关」外全部默认关：

| 授权项 key | 控制什么（人话） | 默认 | 类型 |
|---|---|---|---|
| `cloud_asr` | 语音上云识别（听懂） | false | bool |
| `cloud_llm` | 对话上云（脑子） | false | bool |
| `cloud_vision` | 图片上云（看图） | false | bool |
| `cloud_tts` | 文字上云合成（说话） | false | bool |
| `clipboard_read` | 读剪贴板 | false | bool |
| `screen_capture` | 截屏看屏幕 | false | bool |
| `camera_enabled`（已有） | 看摄像头 | false | bool |
| `screen_awareness`（已有） | 屏幕感知 | false | bool |
| `emergency_passthrough`（已有） | 紧急白名单 | [] | list |
| `per_feature`（已有） | 细项授权 | {} | dict |
| `proactivity_level`（已有） | 主动程度 | 0 | int |
| `guard_outbound`（**保护措施**） | 出网脱敏网关 | **true** | bool |

**消费方接线**（实现要点）：
- `cloud_asr` → `asr/factory.py` 选 cloud 前判 `is_granted("cloud_asr")`，未授权回退本地 FunASR/omni。
- `cloud_llm` → `llm/factory.py` 选 cloud 前判 `is_granted("cloud_llm")`。
- `cloud_vision` → `core.py:364`（`llm.cloud.image_input`）与 `tools/computer.py:474` 判 `is_granted("cloud_vision")`。
- `cloud_tts` → `tts/factory.py` 选 edge/qwen_rt 前判 `is_granted("cloud_tts")`。
- `clipboard_read` → `tools/clipboard.py:44` 读剪贴板前判。
- `screen_capture` → `tools/computer.py` 与 `tools/system_control.py` 截屏前判（并修掉 `system_control` 绕过 `tools.computer.enabled` 总闸的问题）。
- `emergency_passthrough` → `m3/notify.py:80`、`m3/event_trigger.py:162` 改经 `AuthorizationCenter.get()` 读（见 §六）。

---

## 三、开箱引导流程（frontend/src/components/OnboardingWizard.tsx 重做）

共 **6 步**，从「第一次打开的用户」视角，用生活化命名，每步可跳过。

### 第 1 步 · 欢迎
> 「你好，我是小二，你的桌面语音伙伴。接下来 2 分钟，我们定一件最重要的事：**你的数据去哪儿**。每步都能跳过，以后随时在设置里改。」

### 第 2 步 · 联网方式（核心）

**先检测显卡**（二选一，由用户选）：
- 🔍 自动检测：点「开始检测」，提示「约需 5~10 秒」，完成后显示「你的显卡：RTX 4060 · 8GB 显存」。
- ✍️ 手动填写：直接选档位 `8GB 以下 / 8GB / 16GB / 24GB 及以上 / 核显（无独显）`，**8GB 默认选中**（半主流）。检测不到/不填时按 8GB 算。

**按显存给推荐**（8GB 为默认基准）：

| 显存档 | 推荐 | 理由 |
|---|---|---|
| 8GB（默认基准） | ⭐ 联网模式 | 8GB 跑本地局促；联网开箱即用、快 |
| 16GB / 24GB+ | 🏠 全本地模式 | 对话+看图能同时本地跑 |
| 8GB 以下 / 核显 | ⭐ 联网模式 | 本地只能 CPU 慢跑 |

**三个方案卡片**（推荐项高亮⭐，列名用「须知」不用「代价」）：

| 方案 | 一句话 | 须知 |
|---|---|---|
| 🌐 联网模式（开箱即用） | 听懂/思考/说话/看图全云端，快、聪明 | 语音+对话+图片+文字发往阿里云/DeepSeek/微软 |
| 🏠 本地模式（隐私最强） | 全在你电脑本地，不出网，反应比云端稍慢 | 需下载约 11~12GB 模型（约 18~20 分钟），对显存/磁盘有需求 |
| ⚙️ 逐项自定义 | 四项各自选 | — |

选「联网」→ 接领 Key 子步骤（DeepSeek/通义，复用现有领 Key + 填 Key + 连通测试）。
选「自定义」→ 展开四项生活化选择：

| 生活化 | 云端 | 本地 |
|---|---|---|
| 怎么**听懂** | 阿里云 | FunASR（约 1GB）/ MiniCPM-o |
| 用什么**脑子** | DeepSeek/通义 | Ollama **Qwen3-8B**（约 5GB，默认推荐） |
| 怎么**看图** | DeepSeek/Qwen 视觉 | MiniCPM-o 8B（约 5~6GB） |
| 怎么**说话** | 微软/阿里云 | Piper（声库 63MB 已随包）/ MiniCPM-o |

每项旁「隐私说明」🔗 链接，点击弹出（收集什么、发给谁、存多久、如何撤回）。

**8GB 显存如实提示**：本地模式跑「对话 Qwen3-8B + 说话 Piper」最舒服（约 6GB 显存）；要「本地看图」再加 MiniCPM-o 会超 8GB，只能轮流加载、切换慢。全本地全功能需 16GB 显存或 32GB 内存。

### 第 3 步 · 看屏幕 / 读剪贴板（敏感，默认关）
> ☐ 允许我读剪贴板 + 截屏看屏幕（识别跟着第 2 步选的模式走：联网=上云看，本地=本地看）。不勾 = 保持关闭。

### 第 4 步 · 紧急白名单（可选，默认空）
预制「火警 / 烟雾报警 / 燃气泄漏」+ 可手填，用户勾选确认；留空 = 默认没有。

### 第 5 步 · 试麦克风
录一句回放验证（复用现有 `/api/mic/echo`）。

### 第 6 步 · 完成
总结卡（「语音发阿里云 / 对话走本地 / 剪贴板未开启 / 白名单为空…」），保存。

---

## 四、白名单（emergency_passthrough）改生效

**问题**：当前 `m3/notify.py:80`、`m3/event_trigger.py:162` 直接 `config.get("emergency_passthrough")` 绕过授权中心，双路径导致变更不保证生效。

**改法（单一事实来源）**：白名单只存 AuthorizationCenter 的 `authorizations.emergency_passthrough`；主动引擎改经 `AuthorizationCenter.get()` 读；前端改白名单走 `POST /api/authorizations/set` 立即落盘，改完即生效（热加载）。

---

## 五、网关（guard_outbound）默认开

- 出网脱敏网关作为授权项 `guard_outbound`，**默认 true（开）**——它是保护措施，与其他默认关的隐私能力相反。
- **接入主对话**：`agent.py`、`orchestrator/xiao_core.py` 的 `complete()` 发云端前过 `guard_outbound`（当前主对话裸奔，全 backend 仅 audit/gateway/consolidate 三处调用）。
- `obfuscation_mapping` 默认空（零脱敏）需启用基础映射；黑词表扩充。

---

## 六、记忆 / 画像

- 两层命名：**「记忆」**（用户显式「记住…」的规则/事实，`memory.py`）+ **「画像」**（系统自动抽取的偏好/习惯/亲友，`memv1`/`persona`）。
- 记忆条数 `DEFAULT_MAX_ENTRIES` 100 → **500 条**；注入 `DEFAULT_INJECT_LIMIT` 20 → **30 条**。
- 存储保留 300MB 容量滚动（`governance.py`，流水+画像合计），本地自用不强缩期限。

---

## 七、删除方式（部分选择，非一键全清）

| 数据 | 删除方式 |
|---|---|
| 记忆（你教的） | 单条删 · 时间区间删 · 快捷项 · 全清空 |
| 画像（我观察的） | 单条删 · 按类型删（亲友卡/习惯/哀伤标签）· 全清空 |
| 对话流水 / 审计（日志） | 时间区间删 · 按会话删 · 全清空 |

**时间区间删除**（像 Google 删活动、选机票）：两个日历「**从 [开始日期] 到 [结束日期]**」删区间 + 快捷项「最近 30 天 / 90 天 / 1 年 / 全部清空」。

**删除入口需补**：当前仅 v3 记忆有 `/api/memory/clear`；memv4 会话原文、向量画像、审计 fact plane、persona 均无 HTTP 删除入口，需补。

---

## 八、本地模型推荐（8GB 显存）

| 环节 | 默认推荐 | 磁盘 | 显存 | 下载（百兆宽带） |
|---|---|---|---|---|
| 对话（脑子） | **Qwen3-8B**（`ollama run qwen3:8b`） | 约 5GB | 约 5~6GB | 约 8 分钟 |
| 看图 | MiniCPM-o 8B | 约 5~6GB | 约 5~6GB | 约 8 分钟 |
| 听懂 | FunASR（paraformer） | 约 1GB | 约 1GB（CPU 也行） | 约 2 分钟 |
| 说话 | Piper 声库 chaowen | 63MB（已随包） | 忽略 | 0 |

合计约 11~12GB 磁盘、下载约 18~20 分钟。8GB 显存「全本地全功能」同时跑不动（Qwen3-8B + MiniCPM-o 叠加超 8GB），只能最简本地或轮流加载。

**淘汰项**：Gemma3-12B（谷歌、中文弱、8GB 紧、国内下载绕，不推荐）；DeepSeek-R1-8B（推理型、对话慢，不做默认，留给「爱解题」用户自装）。

---

## 九、MiniCPM-o 准确口径（订正）

- MiniCPM-o 是**全模态一体化**本地模型（文本+图像+语音+音频），能「听懂 + 看图 + 说话」，对话能力弱于云端大模型（端侧 8B vs 云端几百 B，差两个量级）。
- 是 **8B 参数**（非项目里 `settings_schema.py:63` 写的「约 9B」），量化后约 5~6GB 显存，不是「9GB」。
- 定位：本地「看图 + 听懂 + 说话」的一体化引擎；「对话」本地仍以 Qwen3-8B 为主、MiniCPM-o 仅离线兜底。

---

## 十、技术订正清单（实施时一并改）

| 位置 | 现状 | 改为 |
|---|---|---|
| `backend/config.py` `OLLAMA_MODEL` | `qwen2.5:7b` | `qwen3:8b` |
| `backend/settings_schema.py:63` | 「约 9B 参数」 | MiniCPM-o 8B 全模态 |
| `backend/settings_schema.py:217` | 「DeepSeek 暂不支持图片」 | DeepSeek Flash Vision 支持多模态 |

---

## 十一、实施范围（待新对话按此执行）

1. **授权中心**：`authorization.py` 扩 12 项 + 各工厂/工具接线（§二）。
2. **开箱引导**：`OnboardingWizard.tsx` 重做 6 步 + 检测交互 + 8GB 推荐（§三）。
3. **白名单**：单一事实来源 + 改生效（§四）。
4. **网关**：`guard_outbound` 默认开 + 接入主对话（§五）。
5. **记忆/画像**：条数 500/注入 30（§六）。
6. **删除**：日期区间删除 + 补 4 个删除入口（§七）。
7. **前端授权面板**：`PermsPanel.tsx` 扩「隐私授权」区，展示/开关 12 项 + 白名单编辑 + 删除交互。
8. **技术订正**：3 处（§十）。

> 约束：只读审计已完成的其余清理（cosyvoice/qwen 删除、死代码、active_model 去重、truncate 抽取）在独立提交中，不与本方案混改；测试后端 `unittest discover -s tests`、前端 `npm run check` + `npm run build`（build 需 danger-full-access）；不 push。
