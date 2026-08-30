# 小二（Xiao）项目开发规范

> 版本 v1.0 ｜ 维护人：项目唯一开发者 ｜ 生效日期：2026-08-30

## 一、核心设计原则：第三使用者视角

假设使用者是拿到分发包、在**陌生环境**中安装运行的另一个人，而非开发者本人或本机。

**每项设计都要回答：**

1. 一个没接触过本项目的人，能否在另一台机器上装起来、跑起来？
2. 默认配置下**开箱即用**吗？还是他得先懂一堆本机约定？

| 类别 | 规则 |
|---|---|
| 路径 | 一律相对路径或可配置项，禁止硬编码本机绝对路径 |
| 依赖 | 在 `requirements.txt` 等声明 + 给安装命令；体积大（本地 ASR、Piper 声库）标注「按需安装」 |
| 密钥与服务地址 | 走 `.env` / `config.yaml` 可配置，默认留空或通用占位符，禁止硬编码真实 key |
| 默认值 | 引擎、模型、端口、音色须有「开箱即用」默认值，装完能跑 |
| 报错 | 缺依赖/key/模型 → 人类可读一步式指引，不抛裸堆栈 |

## 二、引擎分层策略

**播报优先级**（高→低）：Qwen 实时流式（默认，边合成边播、首音低延迟）→ edge-tts（免费云兜底）→ Piper（本地离线保底）→ MiniCPM-o（本地 vLLM-omni，可选，**不作默认**）。

- **已移除**：CosyVoice v3、Qwen-Audio-TTS（按砍云清单）。
- **MiniCPM-o**：走本地 vLLM-omni（`base_url` 默认 `http://localhost:8000`，与云 LLM 同 `base_url+model+key` 三件套），可承担唤醒/识别/播放。

## 三、文档体系

| 文档 | 用途 |
|---|---|
| `docs/PRODUCT.md` | 愿景、哲学、文档地图 |
| `docs/ROADMAP.md` | 里程碑 M0-M6 全量设计 |
| `docs/DESIGN.md` | 技术架构 |
| `docs/specs/EVAL.md` | 对话级验收标准 |
| `docs/specs/*.md` | 各模块设计书（新模块参照 `TEMPLATE.md`） |

**纪律**：写任何模块代码前，**先读**对应模块设计书；未经设计确认的实现不予合并。

## 四、Git 版本控制规范

### 4.1 提交前自检（三要三不要）

| ✅ 要 | ❌ 不要 |
|---|---|
| `git add -p` 或显式指定文件 | `git add .` / `git add -A` |
| 提交前 `git status --ignored` 查暂存区 | 暂存区含 `.env*`(真实key)、`*.pem/*.key/secrets/`、`node_modules/dist/build/`、`.DS_Store/Thumbs.db`、`.idea/.vscode/` |
| 一次提交只解决一个问题 | 关联度不高的改动混入一次提交 |

### 4.2 Commit Message（Conventional Commits）

```
<type>(<scope>): <subject>   # subject 首字母小写，≤50 字符
```
type：`feat` `fix` `docs` `style` `refactor` `perf` `test` `chore` `revert`

### 4.3 提交前说明
每次提交前明确：**①文件清单 ②改动目的与解决的问题 ③相关里程碑**。

### 4.4 密钥保护
凡是 `password =` / `api_key =` / `secret =` / `token =` / 形似密钥的硬编码（`sk-xxx`、`AKIA...`），**必须先替换为 `os.getenv("VAR")`** 才可提交。

### 4.5 特殊文件夹处理

| 类型 | 示例 | 策略 | `.gitignore` |
|---|---|---|---|
| 依赖 | `node_modules/`、`venv/`、`__pycache__/` | 完全忽略，只提交锁文件 | `node_modules/` `venv/` `__pycache__/` |
| 运行时 | `logs/`、`uploads/`、`temp/` | 忽略内容，`.gitkeep` 保骨架 | `logs/*` `!logs/.gitkeep` |
| 本地配置 | `.env*`、`*.local` | 提交 `.env.example`，忽略真实文件 | `.env` `.env.local` `.env.*.local` |
| IDE/系统 | `.vscode/`、`.idea/`、`.DS_Store` | 个人全局忽略，不进项目 | 写入 `~/.gitignore_global` |

**`.env` 例外（写死）**：
- **提交侧**：`.env.example`、`.env.sample`、`.env.template`
- **忽略侧**：`.env`、`.env.local`、`.env.production`、`.env.*.local` 等一切真实密钥文件

**全局忽略（一次性）**：
```bash
git config --global core.excludesfile ~/.gitignore_global
echo ".DS_Store" >> ~/.gitignore_global
echo ".idea/" >> ~/.gitignore_global
echo ".vscode/" >> ~/.gitignore_global
```

### 4.6 项目 `.gitignore` 最小集

```gitignore
# 依赖
node_modules/
venv/
__pycache__/
*.pyc
# 运行时数据
logs/
uploads/
temp/
*.log
# 本地配置（真实密钥）
.env
.env.local
.env.*.local
*.local
!.env.example        # 例外：模板可提交
# 构建产物
dist/
build/
*.egg-info/
# 系统垃圾（全局已兜底，双保险）
.DS_Store
Thumbs.db
```

## 五、AI 协作指引

**我（老板）是唯一开发者**，与 AI 协作开始时应提供以下固定指令。AI 须遵守：

1. **第三使用者视角**：相对路径/配置项、密钥走环境变量、引擎有开箱即用默认值、报错人类可读。
2. **禁止硬编码**：绝对路径和真实密钥字符串不得出现在代码中。
3. **Git**：不用 `git add .`/`-A`，用 `git add -p`/显式文件；提交前查暂存区；Conventional Commits；一次一题。
4. **忽略确认**：`.vscode/`/`.idea/`/`.DS_Store` 已全局忽略，项目内无需重复处理或提醒；`.env.example` 提交、真实 `.env` 不提交。
5. **开发前阅读**：写/改模块代码前先读 `docs/specs/` 对应设计书。

## 附录：变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-08-30 | v1.0 | 合并设计原则、引擎分层、文档体系、Git 规范、AI 协作指引；轻简版（保留全部约束） |
