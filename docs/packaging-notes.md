# 小二（Xiao）· 打包发布说明

> 版本 v1.0 ｜ 2026-08-31 ｜ 打包收口基线（T1a/T1b/T1c）
> 决策口径（老板拍板）：silero VAD + sherpa 唤醒 + bge 语义消歧 + Piper 声库随包；piper-tts 库本体（GPL-3.0）不随包，只随包声库 .onnx 模型文件。

## 一、随包模型清单（四类）

| # | 模型（相对项目根） | 体积 | 用途 | 授权 | 随包判定 |
|---|---|---|---|---|---|
| 1 | `models/silero_vad.onnx` | 1.7MB | 语音断句（vad.engine: silero 默认） | MIT（snakers4） | ✅ 随包 |
| 2 | `models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01/` | ≈36MB | 「小二」本地唤醒 | Apache-2.0 | ✅ 随包（分发包内保留 Apache-2.0 LICENSE/NOTICE） |
| 3 | `models/gateway-semantic/bge-small-zh-v1.5/` | 23.3MB | 出网网关语义消歧（合规底线） | MIT（BAAI） | ✅ 随包 |
| 4 | `models/zh_CN-huayan-medium.onnx`(+.onnx.json) | 60.3MB | Piper 本地 TTS（无网报系统提示） | **Unknown** | ⚠️ 待老板拍板（见 §六） |

## 二、向量后端依赖（T1a 收口结论）

- **主选** `sqlite-vec==0.1.9`，已加入 `requirements.txt`（Win x64 预编译 wheel，py3-none）。
- **许可证**：sqlite-vec 0.1.9 = MIT + Apache-2.0 **双许可**，与项目 MIT 兼容（已核验 wheel 内 METADATA）。
- **兜底**：numpy 暴力 cosine 检索（零新增依赖），`get_vector_store` 探测 sqlite-vec 失败自动回退，**装不上也绝不因缺后端无法启动**。
- **真机验证（2026-08-31）**：本机装入 sqlite-vec 0.1.9 后，`SqliteVecStore` 建表/upsert/查询/invalidate/delete/rebuild 全链路通过。修正两处与官方 API 的差异：
  1. KNN 查询需 `AND k = ?` 约束（JOIN 场景 `LIMIT` 不被 vec0 识别）；
  2. vec0 默认 L2 距离，建表需显式 `distance_metric=cosine` 以对齐 numpy 兜底的余弦相似度语义。
- 新增真机测试 `SqliteVecStoreTest`（`tests/test_vector_store.py`，skipUnless sqlite-vec 可用），当前 `test_vector_store` 8/8 全绿。

## 三、模型授权核验结论（2026-08-31，四项逐项）

| 模型 | 授权 | 来源 | 能否随 MIT 分发包 |
|---|---|---|---|
| silero_vad.onnx | MIT | github.com/snakers4/silero-vad | ✅ 能 |
| sherpa-onnx kws 3.3M | Apache-2.0 | github.com/k2-fsa/sherpa-onnx（LICENSE） | ✅ 能（保留 Apache-2.0 声明） |
| bge-small-zh-v1.5 | MIT | huggingface.co/BAAI/bge-small-zh-v1.5 | ✅ 能 |
| zh_CN-huayan-medium | **Unknown** | rhasspy/piper-voices MODEL_CARD | ⚠️ 需老板拍板 |

> Piper 声库风险说明：huayan/medium 目录 MODEL_CARD 原文 `License: Unknown`，训练数据源 HuaYan_TTS 仓库已 404 不可追溯；piper 官方政策「各声库授权以 MODEL_CARD 为准」。声库 .onnx 授权独立于 piper-tts 库（GPL-3.0），但 huayan 自身授权 Unknown 是独立风险点，不能臆断为可随包。

## 四、打包步骤

1. `powershell -NoProfile -ExecutionPolicy Bypass -File desktop/scripts/prepare-runtime.ps1`：组装内置 Python 运行时（embeddable 3.12.10）+ 装 `requirements.txt`（纯云链路）+ 补齐 `models/`（silero/sherpa/piper/bge 配置文件）。
2. `cd desktop && npm run dist`（= prepare-runtime 后 `electron-builder --win`）产出 NSIS 安装器 `release/Xiao-Setup-0.1.0.exe`。
3. `npm run dist:dir` 产出未打包目录 `release/win-unpacked`（快速验证用）。

### extraResources（desktop/package.json）

打 `backend/`、`run.py`、`config.yaml`、`.env.example`、`requirements.txt`、`requirements-local-asr.txt`、`requirements-local-tts.txt`（本轮补）、`frontend/dist`、`models/`（四类齐全）、`runtime/python`。

### prepare-runtime.ps1 本轮改动

- 新增 bge 配置文件下载（config.json / tokenizer.json / tokenizer_config.json，HF 官方 URL）。
- bge ONNX（model_quantized.onnx）官方 BAAI 仓库默认不含，脚本给「手动预置 / optimum 导出」提示（见 §六 待确认 2）。

## 五、三级场景 + 无网边界

| 场景 | 前提 | 能力 |
|---|---|---|
| ① 开箱无网 | 本地唤醒 + 断句 | sherpa 唤醒「小二」+ Silero VAD 断句 + Piper 本地合成**系统级状态提示**（「我好像断网了」「没听清」） |
| ② 有网 | 云端 API 可用 | ASR/LLM/TTS 全走云端，功能正常 |
| ③ 进一步增强 | 引导装本地大模型 | 引导装 funASR / Ollama / MiniCPM-o 增强离线 |

**明确边界**：无网时仅本地唤醒 + 断句 + 本地合成系统提示；**对话/转写/工作全链路需联网**（ASR/LLM/TTS 云 API）。Piper 不替代云端对话，只保证「无网也能开口报状态」。

## 六、待确认项（P0，需老板拍板）

1. **Piper 声库 huayan-medium 授权 Unknown**：随包有合规风险。可选方向：① 换用授权明确的 Piper 中文声库（piper-voices 中多数声库 MODEL_CARD 有明确 LICENSE）；② 改为「按需下载」不随包（首启在线下载）；③ 其它。**未拍板前，建议该声库暂不随包。**
2. **bge ONNX 来源**：官方 BAAI/bge-small-zh-v1.5 仓库默认不含 ONNX，项目现有 `model_quantized.onnx` 为量化版，来源需核实/指定稳定下载源（或打包机手动预置）。
3. **T1c 干净环境打包**：本机沙箱 electron-builder 缓存目录为空（nsis/winCodeSign 未缓存），NSIS 安装器产出需在脱离沙箱的打包机验证。

## 七、交付状态

| 任务包 | 状态 | 备注 |
|---|---|---|
| T1a sqlite-vec 声明+真机验证 | ✅ 完成 | 2 bug 已修，测试全绿 |
| T1b 四类随包+授权核验 | ⚠️ 部分 | huayan Unknown 待拍板；bge ONNX 来源待确认 |
| T1c electron-builder 跑通 | 🔄 进行中 | dist:dir 沙箱验证中，NSIS 需打包机 |
| T2 M4/M5 物理链路 | ⬜ | 后置，下一阶段 |
