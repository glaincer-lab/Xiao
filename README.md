# 小二（Xiao）· Windows 语音工作助手

[English](README_EN.md) | 简体中文

一个 Windows 桌面常驻的中文语音工作助手：说「小二」唤醒 → 说话实时上屏 → 静音自动提交 → 两段式语音回复（先说明「准备干什么」，执行后再「汇报结果」）。

**本质**：给通用 Agent（DeepSeek Harness，DSH）装上语音前端，做成「语音控制的 Agent 工作台」——不只是查天气、搜网页，还能读文件、写代码、跑命令、多步迭代完成任务。语音层只负责唤醒、识别、路由，最难的部分（agent 循环、编程工具、subagent、知识库）外包给 DSH。

**核心链路**：Sherpa-ONNX 本地中文唤醒（免费、离线、零训练）→ Silero VAD 断句 → 阿里云实时流式识别（云端 fun-asr-flash-8k-realtime 默认 + qwen-audio / qwen3-asr，本地可切 FunASR）→ 路由（聊天走 DeepSeek/通义/OpenAI/GLM/Kimi 云端，或本地 Ollama；干活走 DSH）→ 危险操作语音审批 → 播报（edge-tts 默认兜底，可选 CosyVoice v3 / Qwen-Audio-TTS 付费云、Piper 本地离线、MiniCPM-o 本地 vLLM）。

**多方案管理**：唤醒 / 识别 / 播报 / 大模型四环节均支持多方案（卡片列表 + 新建编辑 + 一键切换，模型名手填、以官方为准）；危险操作（联网 / 写工作区外 / 删除 / 安装 / 改系统）语音审批，DSH 无头模式 fail-closed；长任务后台化 + 完成通知。

技术栈：Python（FastAPI + WebSocket）后端 + React（Vite + TypeScript + Three.js）前端 + Electron 托盘壳；依赖 DeepSeek Harness 作为大脑。

## 关于 DeepSeek Harness

小二不是「从零造的语音大脑」，而是给 **DeepSeek Harness（DSH）** 装上语音前端。

- **DSH 是什么**：一个「一切皆插件」的通用 Agent 框架，负责真正的智能——agent 循环、编程工具（读写文件 / 跑命令）、subagent、workflow、知识库检索等。
- **小二的角色**：只做语音三件事——唤醒、识别、路由；一句话走「聊天」还是「干活(DSH)」由路由层决定。
- **如何桥接**：经 `backend/bridge/`（全系统唯一知道 DSH 的地方）调用 `dsh --profile headless`，多轮上下文由桥接层自己维护；危险操作经 `plugins/xiao-approval-bridge` 审批桥插件回传到语音做审批。
- **DSH 是外部依赖**：本仓库不含任何 DSH 代码，使用前需自行安装 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（本机测试版本 `0.1.1-rc.2`）。

> 一句话：DSH 提供「能改文件、跑命令、多步迭代」的大脑，小二提供语音这层皮。

## 界面预览

![小二 · 语音工作助手界面](docs/screenshot.png)

## 特性

- **唤醒**：本地 Sherpa-ONNX 关键词识别，中文原生「小二」，零训练、离线
- **断句**：Silero VAD（ONNX 推理），精准区分语音与环境噪声
- **识别**：云端阿里云实时流式（fun-asr-flash-8k-realtime 默认 + qwen-audio-3.0-asr-flash-streaming / qwen3-asr-flash-realtime），本地可切 FunASR；多方案可存可切
- **大脑**：DeepSeek / 通义千问 / OpenAI / GLM / Kimi（全 OpenAI 兼容）+ 本地 Ollama，支持 function calling；多方案可存可切
- **DSH 干活**：路由到 DeepSeek Harness 执行真实任务（读写文件、跑命令、多步 Agent 循环），带多轮上下文、语音审批、长任务后台化
- **播报**：5 方案可切——edge-tts 免费云（默认兜底）/ CosyVoice v3 / Qwen-Audio-TTS（付费云，各带 flash/plus 档位）/ Piper 本地离线（保底）/ MiniCPM-o（本地 vLLM）
- **声线动画**：麦克风真实 RMS 电平经 WebSocket 推给前端，画实时声线（非假波纹）
- **两段式回复**：计划回复 → 工具执行 → 结果回复
- **可插拔工具**：联网搜索、打开应用/网址、查天气、设置提醒；预留华为智能家居设备接入
- **UI**：React + Vite + Three.js，三栏布局（左对话 / 中星云状态球 + 实时转写 + 声线动画 / 右输入）+ 玻璃拟态

## 架构

```
[Python 常驻后端]                              [Web 前端]
 麦克风(sounddevice) → 唤醒词(Sherpa-ONNX「小二」)   三栏：左对话气泡 + 中星云状态球
     → VAD 断句(Silero)                              + 实时转写大字 + 右输入框
     → 流式 ASR(云端Paraformer/本地FunASR) ──WS──►  文字实时上屏
     → 路由(router) → chat(DeepSeek 直连) 或 dsh(DSH 干活)
     → 工具执行(可插拔) / DSH 桥(bridge)
     → TTS(edge-tts / CosyVoice v3 / Qwen-Audio-TTS / Piper / MiniCPM-o) ──► 语音播报
```

技术栈：**Python 3.11/3.12 + FastAPI + WebSocket**（后端）｜**React + Vite + TypeScript + Three.js**（前端）。

## 目录结构

```
backend/
  agent.py          两段式回复 Agent（计划→执行→结果），系统提示词/记忆轮数可配
  core.py           音频管线状态机（唤醒→听→处理→休眠）+ 软配置热加载
  main.py           FastAPI 入口 + WebSocket 中继 + REST 接口
  config.py         配置加载（.env + config.yaml）
  settings_schema.py  设置字段注册表（前端据此自动渲染，6 组：唤醒/识别/播报/大模型/执行/权限）
  router.py         路由层（auto/chat/dsh 三档 + 关键词）
  perms.py          权限模型（5 类常驻授权 + 关键词预测 + 待授权）
  tasks.py          长任务后台化（队列 + 并发 + 持久化）
  bridge/           ★ 唯一知道 DSH 的地方（headless CLI + 多轮上下文）
  audio/            mic.py 采集 / vad.py 断句 / wake.py 唤醒词
  asr/              云端 fun-asr-flash-8k-realtime(默认)/qwen-audio/qwen3-asr / 本地 FunASR
  llm/              云端 DeepSeek/通义/OpenAI/GLM/Kimi / 本地 Ollama
  tts/              edge-tts / CosyVoice v3 / Qwen-Audio-TTS / Piper / MiniCPM-o
  tools/            搜索 / 打开应用 / 天气 / 提醒（注册表）
  devices/          设备接入抽象（华为智能家居预留）
  session/state.py  状态机 + 线程安全事件总线
frontend/           React 前端（App + Nebula/声线动画/打字机/设置/权限/任务/工作面板）
desktop/            Electron 壳（托盘常驻 + 自动拉起后端）
plugins/           DSH 薄插件（审批桥 xiao-approval-bridge，注入 DSH 审批瀑布）
config.yaml         非敏感配置
.env.example        密钥模板
```

## 快速开始

### 1. 后端

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
# 编辑 .env，至少填一个 DEEPSEEK_API_KEY 或 DASHSCOPE_API_KEY

python run.py
```

后端启动在 `http://127.0.0.1:8123`（健康检查 `GET /health`）。

### 2. 前端

```powershell
cd frontend
npm install
npm run dev
```

浏览器打开 `http://localhost:5173`（前端 WS 直连后端 8123，无需代理）。

### 3. 使用

- 对麦克风说「小二」唤醒（或点右下角「唤醒」按钮）
- 说话，文字实时上屏；停下约 1.5 秒自动提交
- 助手先播报「准备做什么」，执行后再播报结果
- 也可在右栏输入框打字（Ctrl+回车发送），绕过语音测完整链路

## 语音控制指令

唤醒后说出以下口令（代码级匹配，不走大模型，词表可在 `config.yaml` 改）：

| 口令 | 行为 | 确认 |
|---|---|---|
| 你退下吧 / 你可以回去了 / 退下 / 回去吧 / 睡觉吧 / 再见 | 回待机 + 清空历史 | 否 |
| 清空对话 / 清空历史 / 忘记之前 / 重新开始 | 清空历史 → 留聆听态 | 否 |
| 你把自己关了吧 / 关闭自己 / 退出程序 / 退出吧 | 两步确认 → 退出后端 | 是 |

关闭的两步确认：说关闭口令 → 助手问「确认要完全关闭我吗？」→ 说「确认关闭」退出、说「取消」或 5 秒不答则取消。

## 路由（聊天 / 干活分离）

一句话走哪条通道，由 `backend/router.py` 决定，前端顶栏可手动切换「自动 / 聊天 / DSH」：

| 模式 | 行为 |
|---|---|
| `auto` | 关键词命中（写代码/改代码/实现/修复/调试…）→ DSH；否则 → 聊天 |
| `chat` | 强制聊天（DeepSeek 直连 + 内置工具） |
| `dsh` | 强制走 DSH 干活 |

- DSH 干活支持**多轮上下文**：连续任务会带上前面几轮的任务与结果摘要，让 DSH 记住「之前聊到哪」
- 长任务可后台化，说「进展」问状态、说「取消」停止；完成后语音主动通知
- 危险操作（联网/写工作区外/删除/安装/改系统）走**语音审批**：助手问「允许吗？」，说「允许/拒绝」或点屏幕按钮

## 设置面板

点右上角「设置」，左侧导航 + 右侧滚动，schema 驱动（`backend/settings_schema.py` 定义，前端自动渲染，新增选项只改注册表一行）：

- **唤醒 / 识别 / 播报 / 大模型** 四个环节采用**多方案管理**：每环节是「方案卡片列表」，卡片显示状态图标（✅ 已接 / ⬜ 预留）+ 名称 + 类型，带 `[设为当前]` / `[编辑]` / `[删除]`；`＋ 添加` 打开独立弹窗（编辑时预填、API Key 显示圆点）；当前方案下方就地展开可调属性（音色 / 语速 / 随机度 / 灵敏度）。
- **执行 / 权限**：仍是表单（路由模式、DSH 关键词、命令、超时、并发；审批开关、词表、常驻授权）。
- **音频 / 界面**：麦克风设备下拉（自动枚举）/ 字体、文字大小等前端本地设置。

| 环节 | 已接入（✅） | 预留（⬜） |
|---|---|---|
| 唤醒 | 本地 Sherpa-ONNX「小二」 | 一体化 MiniCPM-o（本地 vLLM） |
| 识别 | 云端 fun-asr-flash-8k-realtime（默认）/ qwen-audio-3.0-asr-flash-streaming / qwen3-asr-flash-realtime、本地 FunASR | 一体化 MiniCPM-o（本地 vLLM） |
| 播报 | edge-tts 免费云（默认）/ CosyVoice v3 / Qwen-Audio-TTS（付费云，flash/plus）/ Piper 本地离线 / MiniCPM-o（本地 vLLM） | — |
| 大模型 | 云端 DeepSeek/通义/OpenAI/GLM/Kimi、本地 Ollama | — |

**实时生效分档**：软配置（可打断、审批词表、DSH 关键词、系统提示词、记忆轮数）保存即生效；引擎类（换方案/模型/音色/阈值/麦克风）保存后提示需重启后端。模型名手填，以各厂商官方文档为准。

## 云端 / 本地切换

| 模块 | 切到本地 | 说明 |
|---|---|---|
| ASR | `pip install -r requirements-local-asr.txt`，识别板块新建/选「本地 FunASR」方案 | FunASR `paraformer-zh`，CPU 较慢 |
| LLM | 装 Ollama 并 `ollama pull qwen2.5:7b`，大模型板块选「本地 Ollama」 | 走 Ollama 的 OpenAI 兼容端点（无需 Key） |

## 扩展：新增一个工具

在 `backend/tools/` 下新建文件，实现 `Tool` 接口并注册：

```python
from backend.tools.base import Tool

class MyTool(Tool):
    name = "my_tool"
    description = "这个工具做什么"
    parameters = {
        "type": "object",
        "properties": {"x": {"type": "string", "description": "参数"}},
        "required": ["x"],
    }

    async def run(self, x: str) -> str:
        return f"执行结果：{x}"
```

然后在 `backend/tools/__init__.py` 的 `register_builtin_tools` 里注册，并把名字加入 `config.yaml` 的 `tools.enabled`。

## 桌面壳（Electron · 常驻托盘）

```powershell
cd frontend && npm run build && cd ..
cd desktop
npm install
npm start
```

- 关闭窗口 = 隐藏到托盘，后端与麦克风监听**持续常驻**
- 托盘菜单：显示/隐藏、开机自启、退出
- 自动拉起后端：优先用项目根 `.venv\Scripts\python.exe`（可用 `XIAO_PYTHON` 指定），后端已在运行则复用

## 已知限制

- **唤醒词**：Sherpa-ONNX 本地模型，改唤醒词需同步改拼音并重启后端
- **首次联网**：唤醒词模型首次需下载；edge-tts 依赖微软在线接口；阿里云 ASR/付费云 TTS 依赖网络
- **麦克风**：需在 Windows「隐私与安全 → 麦克风」允许桌面应用访问
- **DSH 版本**：本机 `0.1.1-rc.2`，桥接层锁版本 + 单一适配（`bridge/`），DSH 升级只改这一处
- **本地 FunASR 体积大**：torch + 模型约数 GB
- **Piper 离线播报**：可选依赖（GPL-3.0 许可证），`pip install -r requirements-local-tts.txt`；中文声库 `zh_CN-huayan-medium.onnx` 已随项目提供于 `models/`
- **MiniCPM-o 播报/识别/唤醒**：需本机启动 vLLM-omni 服务（`llm.omni` 默认 `localhost:8000`），否则无声音

## 文档

- `DESIGN.md`：设计方案（架构 / 状态机 / 路由 / 桥 / 审批 / 风险）
- `ROADMAP.md`：开发路线图（分阶段）
- `AGENTS.md`：项目规则（第三使用者视角 + 引擎分层）
