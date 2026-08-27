# 小二（Xiao）· 项目总结

> 本文是项目当前状态的快照总结，供快速了解「现在是什么样」。规划与分阶段路线见 `ROADMAP.md`，设计细节见 `DESIGN.md`。

---

## 一、项目是什么

**Windows 常驻的中文语音工作助手**。本质是给通用 Agent（DeepSeek Harness，DSH）装上语音前端，做成「语音控制的 Agent 工作台」——不是查天气那种玩具，而是能读写文件、跑命令、多步迭代干活的语音助手。

- **一句话**：DSH 提供「能改文件、跑命令、多步迭代」的大脑，小二提供语音这层皮。
- **仓库**：`glaincer-lab/Xiao`（GitHub 公开，MIT）
- **语音三件事**：唤醒、识别、路由；最难的部分（agent 循环、编程工具、subagent、知识库）外包给 DSH。

## 二、架构与技术栈

```
[Python 后端 FastAPI + WebSocket]          [React 前端 Vite + TS + Three.js]
 麦克风(sounddevice) → 唤醒(Sherpa-ONNX「小二」)
   → VAD 断句(Silero) → 流式 ASR → 路由 → chat(LLM) 或 dsh(DSH 干活)
   → 语音审批(危险操作) → TTS 播报
[Electron 托盘壳]（常驻后台）
```

- 后端入口 `run.py`，端口 `127.0.0.1:8123`（仅回环）
- 前端 WS 直连后端，`cd frontend && npm run dev`
- DSH 是外部依赖（本机 `0.1.1-rc.2`），只经 `backend/bridge/` 一处调用

## 三、语音链路现状（各环节多方案）

| 环节 | 当前方案 | 备选 |
|---|---|---|
| 唤醒 | Sherpa-ONNX 本地「小二」（默认） | MiniCPM-o 一体化（本地 vLLM） |
| ASR | 阿里云 `fun-asr-flash-8k-realtime`（方言，默认） | qwen-audio / qwen3-asr 云端、FunASR 本地 |
| LLM | DeepSeek（`deepseek-v4-pro`） | 通义/OpenAI/GLM/Kimi 云端、Ollama 本地 |
| TTS | **Qwen 实时流式（Ethan）** | 见下 |

## 四、播报引擎（6 方案）

```
Qwen 实时流式（默认）→ edge-tts → CosyVoice v3 → Qwen-Audio-TTS → Piper → MiniCPM-o
```

| 引擎 | 类型 | 音色 | 说明 |
|---|---|---|---|
| **Qwen 实时流式** | 云·流式 | Ethan | 边合成边播，首音约 0.4s，语音紧跟字幕（默认） |
| edge-tts | 免费云 | 云健 `zh-CN-YunjianNeural` | 免费兜底 |
| CosyVoice v3 | 付费云·非流式 | 龙安洋 `longanyang` | 高音质、接受延迟的备选，flash/plus 档位 |
| Qwen-Audio-TTS | 付费云·非流式 | 龙莹松柳 `longyingsongliu` | flash/plus 档位 |
| Piper | 本地离线 | zh_CN-huayan-medium | 断网保底（可选依赖，GPL-3.0） |
| MiniCPM-o | 本地 vLLM | — | `llm.omni` 默认 `localhost:8000` |

## 五、最近完成的工作

1. **回滚聊天模式**：删掉「陪聊小二」（MiniCPM-o 端到端），恢复纯「打工牛马」管线
2. **声线动画**：后端采集麦克风算 RMS 电平，约 100ms 经 WS 推送，前端画真实声线（替换假波纹 `Waveform` 与呼吸球 `Orb`）
3. **TTS 多引擎重构**：从 edge-tts 单一方案扩展为 6 方案，付费云加 flash/plus 档位、音色中文名展示
4. **Piper 预安装**：装 `piper-tts` + 下载声库 `zh_CN-huayan-medium.onnx` 进 `models/`（不随仓库分发）
5. **Qwen 实时流式引擎**：新增 `backend/tts/qwen_realtime.py`，边合成边播，解决「语音比字幕慢」问题
6. **移除音色刷新按钮**：音色全部硬编码，删掉 `/api/tts/voices` 端点和前端动态拉取逻辑
7. **密钥泄露处置**：`config.yaml` 里真实阿里云 key 曾进公开仓库历史 → 清空改走 `.env`、旧 key 已作废、新 key 已写入 `.env`
8. **项目规则**：新增 `AGENTS.md`（第三使用者视角 + 引擎分层）
9. **文档刷新 + QA 审计**：README/README_EN/ROADMAP/DESIGN/OPEN_SOURCE 全部同步；发现并解决 piper-tts GPL-3.0 传染风险（移到 `requirements-local-tts.txt` 可选依赖）

## 六、关键约定与注意事项

1. **密钥**：真实 key 只在 `.env`（已 gitignore），`config.yaml` 一律留空
2. **Git**：schannel SSL 失败，用 `git -c http.sslBackend=openssl`；push 用 gh token 拼 URL（`gh auth token` + `x-access-token:<tok>@github.com`）
3. **Python 环境**：用 `.venv\Scripts\python.exe`（3.12，venv 无 pip，用 `uv` 装包，需设 `UV_CACHE_DIR` 到临时目录）
4. **项目规则**：设计以「第三使用者视角」考虑（可分发、开箱即用、不写死本机路径）
5. **音色命名坑**：三套音色命名互不通用——CosyVoice 短名（`longanyang`）、Qwen-Audio-TTS 带模型前缀（`qwen-audio-3.0-tts-flash-longxxx`）、Qwen 实时流式英文名（Ethan 等）；存储层统一存短名、运行时拼接
6. **重启生效**：引擎类配置改动需重启后端，前端改动刷新页面

## 七、可能的后续方向

- 端到端集成测试 + 打包分发（Electron 安装包）
- MiniCPM-o 本地 vLLM 三角色实测
- `docs/screenshot.png` 界面截图更新
- 中文音色名的官方化核对（当前部分是拼音音译）
