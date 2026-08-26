# 开源方案（定稿）

> 状态：**本地 git 已初始化并提交（3 次），尚未建远程仓库**。许可证 MIT、项目名 Xiao（小二）已定；密钥轮换、素材核查、README 双语等留到「正式开源前」完成。本文件随开发持续维护。

---

## 一、已定决策

| 项 | 决定 |
|---|---|
| 许可证 | **MIT** |
| 项目名 | **Xiao（小二）** —— 英文标识 `Xiao`，中文交互名「小二」 |
| 开源时机 | 基本成熟后；当前只做准备、不发布 |

---

## 二、开源前必须清的三件雷（阻塞项）

### 1. 轮换密钥（暂缓，正式开源前必做）

- `.env` 里有一把真实 DeepSeek API Key（已在 `.gitignore`）。**正式开源前必须去后台重置**（阿里云 `DASHSCOPE_API_KEY` 同理）。
- 开源前跑 `gitleaks detect --source .` 全历史扫描，零告警才 push。
- 铁律：真实 key 只在本机 `.env`，仓库只留 `.env.example`（全占位符）。

### 2. 清理 Picovoice 残留 ✅ 已完成

- `.env.example` 里的 Porcupine/Picovoice 字段已删除；唤醒词统一为 **Sherpa-ONNX KWS 本地中文「小二」**（免费开源，Apache-2.0，零训练）。

### 3. 改名 ✅ 已完成

- 「Jarvis / 贾维斯」（迪士尼商标）已全量替换为「Xiao / 小二」：系统提示词、前端品牌、网页标题、桌面壳、README、包名。

### 附：素材版权（待办）

- `desktop/assets/icon.png`、`tray.png` 需确认来源；开源前换成自有或明确可商用授权的素材。

---

## 三、许可证：MIT（已定）

选 MIT 而非 Apache-2.0 的理由：项目仍处早期、单人维护，MIT 最短最省事、社区无障碍。若后期有「专利条款/商标边界」诉求再迁移 Apache-2.0（MIT 允许重新授权）。

**依赖无传染冲突**（已逐项核对本机 .venv）：

| 依赖 | 许可证 |
|---|---|
| edge-tts / pygame | **LGPLv3**（动态链接、不改源码即可，**不传染**） |
| sherpa-onnx / openai / dashscope | Apache-2.0 |
| onnxruntime / webrtcvad-wheels / fastapi | MIT |
| numpy / requests / beautifulsoup4 / websockets 等 | BSD / Apache / MIT |

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

- [x] `.env` 在 `.gitignore`；`.env.example` 清掉 Picovoice
- [x] `logs/`、模型权重（`*.tflite`、`models/`）加入 `.gitignore`
- [ ] 正式开源前轮换 DeepSeek + 阿里云 key
- [ ] `config.yaml` 去除本机绝对路径
- [ ] 开源前 `gitleaks detect` 全历史扫描，零告警
- [ ] LICENSE（MIT）+ NOTICE 到位

---

## 六、命名

- **Xiao（小二）**：英文标识 `Xiao`，中文交互名「小二」。
- 唤醒词与名字解耦：唤醒词用 **Sherpa-ONNX 本地 KWS 模型**识别中文「小二」，免费、离线、零训练，无需自训练。

---

## 七、发布与社区（分阶段）

### 阶段 0 — 准备（当前，随开发推进）
1. 密钥轮换（正式开源前）＋素材版权核查。
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

> 开源的最大障碍不是技术也不是许可证，而是纪律活：轮换密钥、清残留、改名。其中「清残留 + 改名」已完成，「轮换密钥」留到正式开源前；许可证 MIT、项目名 Xiao（小二）已定。当前专注开发，开源等成熟。
