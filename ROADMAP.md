# 小二（Xiao）· 开发路线图（定版）

> 本文件是开发基线。架构锁定为「混合 C」三层：Python 语音引擎 + DSH 薄插件（JS/TS）+ Electron/React UI。技术栈锁定为：**除唤醒词本地外，ASR/LLM 一律云端 API 优先**。

---

## 一、最终技术栈（锁定）

| 模块 | 选型 | 部署方式 | 说明 |
|---|---|---|---|
| 唤醒词 | Sherpa-ONNX「小二」（3.3MB） | **本地** | 唯一本地组件，零训练、中文原生 |
| VAD 断句 | Silero VAD | 本地（轻量工具） | 语音断句必要组件，非「模型部署」 |
| ASR 主用 | 阿里云实时流式（DashScope API） | **云端 API** | 已有 key，高精度；`fun-asr-flash-8k-realtime`（方言，默认）+ `qwen-audio-3.0-asr-flash-streaming` / `qwen3-asr-flash-realtime`（普通话 16k） |
| ASR 备选 | FunASR 本地 | 本地 | 需 `requirements-local-asr.txt`，torch 数 GB |
| TTS | edge-tts（默认）+ CosyVoice v3 / Qwen-Audio-TTS（付费云）+ Piper（本地）+ MiniCPM-o（本地 vLLM） | 云 + 本地 | 5 方案可切；付费云各带 flash/plus 档位 |
| LLM 主用 | DeepSeek / 千问 / OpenAI / GLM / Kimi | **云端 API** | 全 OpenAI 兼容，UI 可切换 |
| LLM 备选 | Ollama 本地 / MiniCPM-o(vLLM) | 本地 | OpenAI 兼容端点接入 |
| 后端 | FastAPI + WebSocket（Python） | 本地进程 | 异步高性能 |
| 前端 | React + TypeScript + Three.js | Electron 壳 | 星云背景 + 对话界面 |
| DSH 集成 | 薄插件（JS/TS，只做桥接） | 随 DSH | 不改 DSH 核心 |

> **核心原则**：唤醒词本地（常驻、低延迟、离线必须）；其余一律 API 优先（用户已有 DeepSeek/千问/DashScope key，省去本地部署麻烦）。本地 FunASR / Ollama 作为备选已接（OpenAI 兼容端点）；播报另加 Piper 本地离线作断网保底、MiniCPM-o 走本地 vLLM。多方案通过 `models[] + active` 切换。

---

## 二、开发路线图（分阶段 · 含完成状态）

> 状态说明：✅ 已落地并验证 · 🟡 部分完成 · ⬜ 未开始。进度以代码为准，本表随实现更新。

| 阶段 | 内容 | 交付物 | 状态 |
|---|---|---|---|
| **Phase 0** | 唤醒词本地化：openWakeWord → **Sherpa-ONNX「小二」** | `wake.py` + 模型接入 + 灵敏度可配 | ✅ |
| **Phase 1** | 云端主用链路定版：Paraformer ASR + DeepSeek/千问 LLM 全链路跑通 | config「云端主用 + 本地留口」 | ✅ |
| **Phase 2** | UI 升级：Three.js 星云 + 三栏（左对话/中状态球/右输入）+ 打字机 | React 前端改造 | ✅ |
| **Phase 3** | 配置面板：唤醒/语音/执行 + 底部日志 + 状态灯 | 配置面板 UI + 持久化 | ✅ 已升级为 schema 驱动 8 页（见下） |
| **Phase 4** | 路由双通道 + DSH 桥（A1）端到端 | 语音→路由→DSH→语音 全链路 | ✅ |
| **Phase 5** | DSH 薄插件：语音桥 + 审批转发（XIAO_GRANT 环境变量） | 桥接跑通 | ✅ |
| **Phase 6** | 语音审批：`AWAIT_APPROVAL` + 屏幕按钮/语音「允许/拒绝」 | 语音审批可用 | ✅ |
| **Phase 7** | 长任务后台化：任务列表 + 后台跑/进展/取消 + 完成通知 | 异步长任务体验 | ✅ |
| **Phase 8** | 集成测试 + 打包 + 文档 | 可分发版本 | 🟡 文档已刷新，打包/集成测试待做 |

### 已落地但超出原始规划的功能

| 功能 | 说明 | 状态 |
|---|---|---|
| 设置面板 8 页 schema 驱动 | `backend/settings_schema.py` 字段注册表（32 字段 6 组 + 音频/界面），前端自动渲染 | ✅ |
| 系统提示词 / 记忆轮数可配 | `agent.system_prompt` / `agent.max_history` 读 config，一键清空记忆 | ✅ |
| 麦克风设备下拉 + TTS 试听 | `sounddevice` 枚举输入设备；edge-tts 试听 | ✅ |
| 软配置热加载 | 可打断/审批词表/DSH 关键词/提示词等保存即生效，引擎类提示重启 | ✅ |
| 多轮 DSH 上下文 | `bridge/` 记录最近任务与结果摘要，每轮打包传给 headless DSH | ✅ |
| 星云状态形态 | 状态→3D 形态映射（待机球体/聆听八面体/思考∞/干活螺旋/播报圆柱/审批立方体） | ✅ |

---

## 三、依赖与前置条件

| 依赖 | 状态 | 说明 |
|---|---|---|
| Sherpa-ONNX（KWS 中文模型） | ✅ 已接入 | Phase 0（`wake.py`，本地「小二」） |
| DashScope key（Paraformer ASR） | ✅ 已有 | `.env` 的 `DASHSCOPE_API_KEY` |
| DeepSeek / 千问 key | ✅ 已有 | OpenAI 兼容端点 |
| DSH | ✅ 已装 | 本机 `0.1.1-rc.2`；薄插件随 DSH 升级迭代 |
| Electron 环境 | ✅ 已有 | `desktop/` |

> **🔒 安全红线（A2 审批）**：`POST /api/respond`（审批注入端点）**永远只绑回环**（127.0.0.1），`trustedHosts` 不得外放（不可配成 0.0.0.0/局域网）。「无认证」仅对「本机单用户」成立；开源分发时此条写进 SECURITY.md。

---

## 四、本期明确「不做」的项（参考，可自定义）

| 项 | 状态 | 何时做 |
|---|---|---|
| MiniCPM-o 一体化语音（唤醒/识别/播报） | 🟡 播报/识别/唤醒三角色已接入（走本地 vLLM，端口预设 localhost:8000），未做端到端一体对话 | 需要端到端一体语音时 |
| 付费云 TTS / 本地 Piper | ✅ 已接入（CosyVoice v3 / Qwen-Audio-TTS 付费云 + Piper 本地离线保底） | — |
| 华为智能家居 | 不实现 | 后期 |
| 多语言唤醒 | 不实现 | 后期 |

---

## 五、一句话总结

> 唤醒词本地「小二」（Sherpa-ONNX），其余全走云端 API（Paraformer ASR + DeepSeek/千问 LLM + edge-tts TTS），Python 语音引擎 + DSH 薄插件 + Electron 星云界面，分 8 个阶段、约 22 天落地。
