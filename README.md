# 小二（Xiao）· 生活在你电脑里的伙伴

[English](README_EN.md) | 简体中文

一个 **Windows 桌面常驻的中文语音陪伴伙伴**——但它不止于助手。

**它是一台语音优先的桌面陪伴型个人 AI 伙伴**：语音是最自然的入口，而它的价值在**记得住、有情绪、会主动、能行动**。

**长期愿景**：助手、朋友、伙伴。帮你干活，也记得你的成长、陪你度过低落、在合适的时候先开口——一个由程序实现的伙伴，而不是一个听话的工具。

## 我们的三条产品哲学

这是小二一切设计决策的出发点：

1. **记忆要像伙伴的记性**——记得住、容得下变化、从不拿记忆跟你对质。对细节学会健忘，对情绪保持深刻。
2. **主动但克制**——没有记忆的主动是骚扰，有记忆的主动才是伙伴。所有主动开口收口在一个总闸门，说废话不如沉默。
3. **感知先于行动，行动必有验证**——控设备前先查状态，执行后读回确认。主动只建议，动手必审批。

围绕这三条，我们公开全部设计思考：[产品设计书](docs/PRODUCT.md) ｜ [路线图 M0-M6](docs/ROADMAP.md)

## 当前能力（v1 已落地）

- **完整中文语音链**：说「小二」唤醒（本地 Sherpa-ONNX）→ 实时上屏 → 静音自动提交 → 两段式语音回复（先说准备干什么，再汇报结果）；播报四方案可切（Qwen 实时流式默认，首音约 0.4s）
- **干活大脑（DSH 桥）**：语音驱动的多步任务——读文件、写代码、跑命令，实时进度反馈、长任务后台并发、危险操作语音审批
- **语音操电脑**：鼠标/打字/热键/窗口/截屏看图/UIA 六工具，默认关、逐类语音审批（说「允许」才动）
- **生活指令免 Key 可用**：开应用、调音量、查天气、定提醒、读剪贴板等 14 类口令，不配任何 Key 也能跑
- **长期记忆起步**：「记住…」跨会话保存，新对话自动想起
- **出网安全网关**：所有云调用的强制前置层——敏感词本机拦截、人名占位混淆、还原校验
- **离线兜底**：全链路可切本地（识别 FunASR / 大脑 Ollama / 播报 Piper），断网可用；「离线就绪」灯一眼可查
- **开箱即用**：首次启动向导（领 Key → 连通测试 → 零配置进入基础功能）；安装包内置 Python 运行时，使用者无需装任何环境

## 核心后端模块一览

| 模块 | 里程碑/任务包 | 作用 | 设计书 |
|---|---|---|---|
| 跨模块事件总线 | M0 | 模块间语义化解耦（唯一通信信道） | [EVENT_REGISTRY](docs/specs/EVENT_REGISTRY.md) |
| 宏观四态状态机 | M0 · T5 | ACTIVE/IDLE/DORMANT/RETURNING；DORMANT 冻结主动、零归因 | — |
| 授权中心 | M0 · T6 | 敏感能力授权统一收口（摄像头/屏幕/主动度/紧急穿透/细项），默认全关 | — |
| 出网安全网关 | M0 | 所有云调用前置：黑词本机拦截、人名占位混淆出云、还原校验 | — |
| 任务编排层 | T7 | 贵模型规划 + 廉价模型执行的 DAG 节点流转（复杂任务提质降本） | [T7-orchestrator](docs/specs/T7-orchestrator.md) |
| 可审计回放层 | T8 | 桥事件 append-only 落盘，按 run 回放时间线 + tool/result 质量打点 | [T8-audit](docs/specs/T8-audit.md) |
| 数据备份 | T4 | 每日快照 logs/ 数据目录（SHA-256 校验、保留 7 天） | `scripts/backup.py` |

> T7 任务编排层与 T8 可审计回放层的设计思想受 [xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）启发，为小二自研实现（不复制其代码）。

## 路线图一览

| 里程碑 | 一句话 |
|---|---|
| M0 横切基建 | 跨模块事件总线（已实现）、宏观四态状态机（已实现 T5）、授权中心（已实现 T6）、**出网安全网关**（已实现，黑词本机拦截+人名混淆出云）、注意力传感器（设计态） |
| M1 记住 | 记忆工程：分层记忆+冲突协议+人设世界观+**亲友人物卡**（业界空白） |
| M2 有心 | 情感状态机+八种沟通姿态+建设性冲突（会温和地说"不"） |
| M3 会主动 | 主动引擎：每日预算制心跳+事件触发+总闸门滑块 |
| M4 看得见 | 门控观察会话：看穿搭给建议（按需抽帧、帧即焚、只存结论） |
| M5 动得了 | 智能家居三层接入（Home Assistant 优先）+ 行程饮食建议编排 |
| M6 陪你长大 | **双向成长记录**（业界空白）：你的里程碑和它的里程碑；记忆全量导出——搬家不失忆 |

隐私与安全是伙伴感的地基：摄像头默认关、观察帧会话结束即焚、出网内容过安全网关（人名混淆、敏感词不出网）、记忆本地存储可随时导出删除。详见路线图 §1.5。

## 快速开始

### 开发运行

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # 填一个 DEEPSEEK_API_KEY 或 DASHSCOPE_API_KEY
python run.py            # 后端 http://127.0.0.1:8123

cd frontend && npm install && npm run dev   # 前端 http://localhost:5173
```

### 测试与验证

```powershell
# 单测（M0-M2 主线，352 项全绿）
python -m unittest tests.test_event_bus tests.test_macro_state tests.test_authorization tests.test_gateway tests.test_blocklist tests.test_obfuscate tests.test_semantic_filter tests.test_session_manifest  # M0: 121
python -m unittest tests.test_memv4 tests.test_memv1_conflict tests.test_memv1_consolidate tests.test_memv1_mishearing tests.test_memv1_retrieval tests.test_memv1_persona  # M1: 116
python -m unittest tests.test_memv2_posture tests.test_memv2_affect tests.test_memv2_phrases tests.test_memv2_shadow tests.test_memv2_bridge tests.test_memv2_attack  # M2: 115

# 任务包补强：模块边界 / T7 编排 / T8 审计 / T4 备份
python scripts/audit_module_boundaries.py                  # T0: 模块边界纪律，期望 PASS
python -m unittest tests.test_orchestrator                 # T7: 任务编排层（18 项）
python -m unittest tests.test_xiao_audit                   # T8: 可审计回放层（20 项）
python -m unittest tests.test_backup                       # T4: 备份脚本（SHA-256/保留 7 天）
```

对麦克风说「小二」唤醒；或右栏打字（Ctrl+回车）绕过语音测全链路。

### 打包分发

```powershell
cd frontend && npm run build && cd ..
cd desktop && npm install && npm run dist
```

产物 `desktop/release/Xiao-Setup-*.exe`：免管理员安装、内置 Python 与模型，使用者双击即用，无需任何环境。

## 交互示例

| 你说 | 小二做 |
|---|---|
| 小二，今天天气怎么样 | 查天气播报（免 Key） |
| 小二，帮我写个脚本整理下载文件夹 | 路由到 DSH：说明计划→执行→语音汇报，危险步骤先请示 |
| 小二，记住我喜欢简短回答 | 存入长期记忆，之后永久生效 |
| 小二，按 Ctrl+S | 语音操电脑（先问允许，说「允许」才执行） |

## 架构一句话

Python 音频管线（唤醒→VAD→流式 ASR）+ 跨模块事件总线（模块间解耦）+ 路由（聊天走云端 LLM / 干活走 DSH）+ TTS 播报；React + Three.js 星云界面；Electron 托盘常驻。技术细节见 [DESIGN.md](docs/DESIGN.md)。

## 已知限制

- 唤醒词为本地模型「小二」，改词需改拼音并重启
- 云端 ASR/LLM/TTS 依赖网络与自备 Key；全离线需另装本地引擎（FunASR 数 GB）
- 麦克风需在 Windows 隐私设置中允许桌面应用访问
- DSH 为外部依赖（已验证 `0.1.1-rc.2`），需自行安装

## 文档

- [产品设计书](docs/PRODUCT.md)——愿景、哲学、文档地图
- [路线图](docs/ROADMAP.md)——M0-M6 全量设计与当前进度

## 许可证

MIT（本地 Piper 声库为 GPL-3.0 可选依赖，见 `NOTICE`）。
