# 开源方案（定稿）

> 状态：**已开源到 GitHub（glaincer-lab/Xiao，MIT）**。密钥轮换见 §二（暂缓）；素材版权已确认无风险；本文件随开发持续维护。

---

## 一、已定决策

| 项 | 决定 |
|---|---|
| 许可证 | **MIT** |
| 项目名 | **Xiao（小二）** —— 英文标识 `Xiao`，中文交互名「小二」 |
| 开源时机 | 基本成熟后；当前只做准备、不发布 |

---

## 二、开源前必须清的三件雷（阻塞项）

### 1. 轮换密钥（✅ 已处理，2026-08）

- **事故**：`config.yaml` 曾明文写入阿里云百炼 Key（`sk-ws-H.EDHHXLP...`），并随公开仓库历史提交（`3a3e9d7` 起），任何 clone 者可读。
- **处置**：① `config.yaml` 明文 key 全部清空，改走 `.env`（已 gitignore）；② 该泄露 key 已在阿里云后台**作废**；③ 新 key 已写入本机 `.env` 并验证可用。
- **遗留**：git 历史中仍含旧 key 的提交记录（旧 key 已作废，无资金风险，但若追求彻底需重写历史）。DeepSeek key 仅在本机 `.env`，未进仓。
- 铁律：真实 key 只在本机 `.env`，仓库只留 `.env.example`（全占位符）。

### 2. 清理 Picovoice 残留 ✅ 已完成

- `.env.example` 里的 Porcupine/Picovoice 字段已删除；唤醒词统一为 **Sherpa-ONNX KWS 本地中文「小二」**（免费开源，Apache-2.0，零训练）。

### 3. 改名 ✅ 已完成

- 「Jarvis / 贾维斯」（迪士尼商标）已全量替换为「Xiao / 小二」：系统提示词、前端品牌、网页标题、桌面壳、README、包名。

### 附：素材版权（✅ 已确认无风险）

- `desktop/assets/icon.png`、`tray.png` 为自制抽象图形（蓝色同心圆），无第三方版权风险，保留使用。

---

## 三、许可证：MIT（已定）

选 MIT 而非 Apache-2.0 的理由：项目仍处早期、单人维护，MIT 最短最省事、社区无障碍。若后期有「专利条款/商标边界」诉求再迁移 Apache-2.0（MIT 允许重新授权）。

**依赖无传染冲突**（已逐项核对本机 .venv）：

| 依赖 | 许可证 |
|---|---|
| edge-tts / pygame | **LGPLv3**（动态链接、不改源码即可，**不传染**） |
| piper-tts | **GPL-3.0-or-later**（⚠️ 强传染，见下方说明） |
| sherpa-onnx / openai / dashscope | Apache-2.0 |
| onnxruntime / webrtcvad-wheels / fastapi | MIT |
| numpy / requests / beautifulsoup4 / websockets 等 | BSD / Apache / MIT |

> ⚠️ **piper-tts（GPL-3.0）风险**：piper-tts 是 GPL-3.0-or-later 强传染许可证。本项目 MIT 主仓库**不应把 piper-tts 作为硬依赖分发**——它应是用户「按需可选安装」的本地 TTS 引擎（当前 `requirements.txt` 已列为可选，代码里 import 是惰性/兜底）。若随仓库强制分发，MIT 与 GPL 的传染性会起冲突。保守做法：把 piper-tts 移到「可选依赖」单独文件，README 注明「仅离线保底场景按需安装」。

### 模型权重：单独声明，不随仓库分发

| 模型 | 许可证 | 处理 |
|---|---|---|
| Sherpa-ONNX KWS 中文模型（3.3MB） | Apache-2.0 | 可随仓或让用户下载 |
| Silero VAD 模型 | 非商用免费，商用需授权 | 不随仓，文档注明 |
| Paraformer 本地模型（FunASR） | 非商用免费，商用需阿里授权 | 不随仓，默认走云端 DashScope |
| Paraformer 实时 / edge-tts 语音 | 云服务 ToS | 用户自备 key |

> 写入 `NOTICE`：声明「本仓库只分发代码，不分发受非商用限制的模型权重」。

---

## 四、仓库结构与 DSH 解耦

### 结构：单仓库（monorepo），先不拆

```
xiao/
├── backend/            Python：音频/ASR/LLM/TTS/工具/路由/桥
├── frontend/           React 工作台
├── desktop/            Electron 壳（可选）
├── docs/               DESIGN.md 等
├── README.md           ← 中英双语
├── LICENSE             ← MIT
├── NOTICE              ← 模型/依赖许可声明
├── CONTRIBUTING.md
├── SECURITY.md
├── CHANGELOG.md
├── config.yaml         ← 只放默认值，不写死本机路径
├── .env.example        ← 纯占位符
└── .gitignore
```

### DSH 解耦声明（对外讲清楚）

- DSH 是**外部依赖**，用户自己安装。README 明确写「前置条件：先装 DeepSeek Harness」。
- 本仓库**不含任何 DSH 代码**，只通过 `backend/bridge/` 一个模块调用它的 CLI/RPC。
- `config.yaml` 的 `bridge.dsh_command`、`agent.workspace` 不写死本机绝对路径，做成可配置（默认相对路径）。

---

## 五、配置与密钥卫生（清单）

- [x] `.env` 在 `.gitignore`；`.env.example` 为纯占位符
- [x] `logs/`、模型权重（`models/`）、`.gh-config/` 加入 `.gitignore`
- [x] `config.yaml` 工作目录改为相对路径（不写死本机绝对路径）
- [x] 全历史密钥扫描（git 历史 + 正则），零泄露
- [x] 轮换阿里云 key（泄露 key 已作废，新 key 已写入 .env）
- [x] LICENSE（MIT）
- [x] NOTICE（模型/依赖许可声明）
- [ ] piper-tts 移到可选依赖（GPL-3.0 传染风险，见 §三）

---

## 六、命名

- **Xiao（小二）**：英文标识 `Xiao`，中文交互名「小二」。
- 唤醒词与名字解耦：唤醒词用 **Sherpa-ONNX 本地 KWS 模型**识别中文「小二」，免费、离线、零训练，无需自训练。

---

## 七、发布与社区（分阶段）

### 阶段 0 — 准备（当前，随开发推进）
1. 密钥轮换（正式开源前）；素材版权已确认无风险。
2. 写 NOTICE；README 中英双语。

### 阶段 1 — 首版开源（基本成熟后）
1. README（中英）＋架构图＋安装步骤＋前置依赖（DSH）＋演示 GIF。
2. 打 tag `v0.1.0`，写 CHANGELOG。
3. 只开源「语音链路 + chat 通道 + A1 桥」，DSH 审批/流式桥标「实验性」。

### 阶段 2 — 社区化
1. CONTRIBUTING（DCO）＋Issue/PR 模板＋SECURITY.md。
2. CI：lint + 单测 + 构建检查。

### 阶段 3 — 推广
1. 30–60 秒演示视频（语音唤起→下任务→Agent 改文件→语音汇报）。
2. 投中文社区 + GitHub trending + awesome-* 收录。
3. 差异化标签：「把语音接进通用编程 Agent 的开源工作台」。

---

## 八、一句话总结

> 开源的最大障碍不是技术也不是许可证，而是纪律活：轮换密钥、清残留、改名。其中「清残留 + 改名 + 密钥检查」已完成；许可证 MIT、项目名 Xiao（小二）。

## 九、开源完成小结（2026-08）

已开源：**https://github.com/glaincer-lab/Xiao**（MIT）

- 仓库内容：后端 + 前端 + Electron 壳 + 审批桥插件 + 文档 + 测试集
- 开源前清理：openwakeword/tflite 残留、本机绝对路径、日志/任务记录、个人信息——全部清除
- 密钥：确认未进仓、git 历史无 key（4 重验证）；轮换暂缓（无资金风险）
- 文档：README（含界面截图 + 完整描述）、DESIGN、ROADMAP、LICENSE、NOTICE
- Topics：voice-assistant / agent / speech-recognition / deepseek / windows / chinese
- 素材：图标为自制抽象图形，版权无风险
