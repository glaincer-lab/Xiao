# 小二（Xiao）项目规则

## 核心设计原则：第三使用者视角

本项目的所有设计与实现，必须以「第三使用者」的视角来考虑——即假设使用者
**不是**开发者本人、**不是**当前这台开发机，而是拿到分发包、在陌生环境里
安装使用的另一个人。

因此，任何设计决策都要同时回答两个问题：

1. 一个从没碰过这个项目的人，能不能在**另一台机器**上装起来、跑起来？
2. 默认配置下，**开箱即用吗**？还是需要他先懂一堆本机特有的约定才能用？

具体落地到工程：

- **路径**：一律相对路径或可配置项，禁止写死本机绝对路径（如 `D:\...`）。
- **依赖**：在 requirements / 文档里声明，并给出安装命令；体积大的（本地 ASR、Piper 声库等）标注「按需安装」。
- **密钥与服务地址**：走 `.env` / `config.yaml` 可配置项，默认留空或给通用默认值，禁止硬编码任何人的真实 key。
- **默认值**：引擎、模型、端口、音色都要有「开箱即用」的默认值，做到「装完就能跑」。
- **报错提示**：缺依赖、缺 key、缺模型时，给出人能看懂的一步式提示，而不是只抛异常堆栈。

## 引擎分层（云 / 本地 / 保底）

- 播报引擎：`Qwen 实时流式`（默认，边合成边播、首音快、语音跟字幕）→ `edge-tts`（免费云兜底）→ `Piper`（本地离线，最后保底）→ `MiniCPM-o`（本地 vLLM，可选）。CosyVoice v3 / Qwen-Audio-TTS 已按砍云清单移除。
- MiniCPM-o 走本地 vLLM-omni 服务接入（`llm.omni` 的 `base_url` 默认 `localhost:8000`，与云 LLM 同样的 base_url + model + key 三件套），可分别承担唤醒 / 识别 / 播放，不作为默认引擎。

## 文档体系

产品愿景与设计哲学见 `docs/PRODUCT.md`（入口与文档地图）；里程碑全量设计（M0-M6）见 `docs/ROADMAP.md`；技术架构见 `docs/DESIGN.md`；对话级验收标准见 `docs/specs/EVAL.md`；模块设计书位于 `docs/specs/`（模板 `TEMPLATE.md`）。写代码前先读对应模块设计书。

## Git 提交与仓库管理铁律（强制）

1. **提交前自检**：严禁 `git add .` / `git add -A`，只 `git add <具体文件>` 或 `git add -p`。暂存区若含 `.env*`、`*.pem`、`*.key`、`secrets/`、`node_modules/`、`dist/`、`.DS_Store`、`.idea/`、`.vscode/`，立即中断并警告。
2. **Commit Message（Conventional Commits）**：格式 `<type>(<scope>): <subject>`，subject 首字母小写、≤50 字符。type ∈ `feat|fix|docs|style|refactor|perf|test|chore|revert`。
3. **原子性**：一次提交只解决一个问题；关联度不高的改动分次提交。提交前先说明文件清单 + 改动目的。
4. **密钥保护**：含密钥字符串（`password = `、`api_key = `、`secret = `）的变更，须先替换为 `os.getenv("VAR")` 才可提交。
