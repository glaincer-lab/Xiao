# 小二（Xiao）· 打包发布说明

> 版本 v1.1 ｜ 2026-08-31 ｜ 打包收口基线（T1a/T1b/T1c）
> 决策口径（老板拍板）：silero VAD + sherpa 唤醒 + bge 语义消歧 + Piper 声库随包；piper-tts 库本体（GPL-3.0）不随包，只随包声库 .onnx 模型文件。

## 一、随包模型清单（四类）

| # | 模型（相对项目根） | 体积 | 用途 | 授权 | 随包判定 |
|---|---|---|---|---|---|
| 1 | `models/silero_vad.onnx` | 1.7MB | 语音断句（vad.engine: silero 默认） | MIT（snakers4） | ✅ 随包 |
| 2 | `models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01/` | ≈36MB | 「小二」本地唤醒 | Apache-2.0 | ✅ 随包（分发包内保留 Apache-2.0 LICENSE/NOTICE） |
| 3 | `models/gateway-semantic/bge-small-zh-v1.5/` | 22.9MB | 出网网关语义消歧（合规底线） | MIT（BAAI，ONNX 经 Xenova 转换） | ✅ 随包 |
| 4 | `models/zh_CN-chaowen-medium.onnx`(+.onnx.json) | 60.3MB | Piper 本地 TTS（无网报系统提示） | CC0（OHF-Voice 数据集） | ✅ 随包（见 §六 灰色地带留痕） |

## 二、向量后端依赖（T1a 收口结论）

- **主选** `sqlite-vec==0.1.9`，已加入 `requirements.txt`（Win x64 预编译 wheel，py3-none）。
- **许可证**：sqlite-vec 0.1.9 = MIT + Apache-2.0 **双许可**，与项目 MIT 兼容（已核验 wheel 内 METADATA）。
- **兜底**：numpy 暴力 cosine 检索（零新增依赖），`get_vector_store` 探测失败自动回退，**装不上也绝不因缺后端无法启动**。
- **真机验证（2026-08-31）**：本机装入 sqlite-vec 0.1.9，`SqliteVecStore` 全链路通过。修正两处与官方 API 的差异：① KNN 查询需 `AND k = ?`（JOIN 场景 `LIMIT` 不被识别）；② vec0 默认 L2，需显式 `distance_metric=cosine` 对齐 numpy 余弦相似度。
- 新增真机测试 `SqliteVecStoreTest`，`test_vector_store` 8/8 全绿。

## 三、模型授权核验结论（2026-08-31，四项逐项）

| 模型 | 授权 | 来源 | 能否随 MIT 分发包 |
|---|---|---|---|
| silero_vad.onnx | MIT | github.com/snakers4/silero-vad | ✅ 能 |
| sherpa-onnx kws 3.3M | Apache-2.0 | github.com/k2-fsa/sherpa-onnx（LICENSE） | ✅ 能（保留 Apache-2.0 声明） |
| bge-small-zh-v1.5 | MIT（原模型）；Xenova ONNX 继承 | huggingface.co/BAAI/bge-small-zh-v1.5 | ✅ 能（NOTICE 注明继承自 BAAI MIT） |
| zh_CN-chaowen-medium | **CC0**（MODEL_CARD Dataset License） | rhasspy/piper-voices | ✅ 能（见 §六） |

> **bge ONNX 来源**：官方 BAAI/bge-small-zh-v1.5 仓库不含 ONNX，故采用 **Xenova/bge-small-zh-v1.5** 的单文件量化 ONNX（model_quantized.onnx，22.9MB，输出 last_hidden_state 512 维，已核验与 semantic_filter.py 兼容）。Xenova 仓库自身 license 字段为 null，ONNX 为纯格式转换、不产生新 IP，许可继承原模型 BAAI MIT；项目 NOTICE 已注明。
> 下载直链：`https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/main/onnx/model_quantized.onnx`

## 四、打包步骤

1. `powershell -NoProfile -ExecutionPolicy Bypass -File desktop/scripts/prepare-runtime.ps1`：组装内置 Python 运行时（embeddable 3.12.10）+ 装 `requirements.txt` + 补齐 `models/`（silero / sherpa / piper-chaowen / bge-Xenova）。
2. `cd desktop && npm run dist`（= prepare-runtime 后 `electron-builder --win`）产出 NSIS 安装器 `release/Xiao-Setup-0.1.0.exe`。
3. `npm run dist:dir` 产出未打包目录 `release/win-unpacked`（快速验证）。

### extraResources（desktop/package.json）

打 `backend/`、`run.py`、`config.yaml`、`.env.example`、`requirements.txt`、`requirements-local-asr.txt`、`requirements-local-tts.txt`、`frontend/dist`、`models/`（四类齐全）、`runtime/python`。

### prepare-runtime.ps1 本轮改动

- 修复 UTF-8 BOM 缺失（Windows PowerShell 5.1 按 GBK 误读中文导致 ParserError）。
- 新增 bge 下载（tokenizer 从 BAAI，ONNX 从 Xenova 直链）。
- Piper 声库 huayan（Unknown）→ chaowen（CC0）。

## 五、三级场景 + 无网边界

| 场景 | 前提 | 能力 |
|---|---|---|
| ① 开箱无网 | 本地唤醒 + 断句 | sherpa 唤醒「小二」+ Silero VAD 断句 + Piper 本地合成**系统级状态提示** |
| ② 有网 | 云端 API 可用 | ASR/LLM/TTS 全走云端，功能正常 |
| ③ 进一步增强 | 引导装本地大模型 | 引导装 funASR / Ollama / MiniCPM-o 增强离线 |

**明确边界**：无网时仅本地唤醒 + 断句 + 本地合成系统提示；**对话/转写/工作全链路需联网**。Piper 不替代云端对话，只保证「无网也能开口报状态」。

## 六、合规留痕（B 类，已按 MODEL_CARD 判定）

1. **chaowen 声库授权**：MODEL_CARD `Dataset` 节 `License: CC0`，数据集 OHF-Voice/voice-datasets（README 明确 CC0 public domain）。⚠️ 灰色地带：MODEL_CARD 标注 "Finetuned from Xiao Ya voice"，而 xiao_ya 数据集为 non-commercial（BZNSYP）——chaowen 官方声明 CC0，但从 xiao_ya 权重微调，license 继承存在灰色地带。**这是 zh_CN 下唯一宽松许可候选**（其余 huayan Unknown、xiao_ya non-commercial 均排除），故采用 chaowen 随包；项目 NOTICE 注明「声库基于 CC0 数据集（OHF-Voice），来源 rhasspy/piper-voices」。若需 100% 严格，可向 piper-voices 上游确认 chaowen 权重是否完全脱离 xiao_ya 的 non-commercial 约束。
2. **bge ONNX**：Xenova 仓库 license 字段 null，许可继承原模型 BAAI MIT；NOTICE 注明「ONNX 权重继承自 BAAI/bge-small-zh-v1.5（MIT）」。
3. **sherpa kws**：Apache-2.0，分发包内须保留其 LICENSE/NOTICE 声明。

## 七、交付状态

| 任务包 | 状态 | 备注 |
|---|---|---|
| T1a sqlite-vec 声明+真机验证 | ✅ 完成 | 2 bug 已修，测试全绿 |
| T1b 四类随包+授权核验 | ✅ 完成 | chaowen CC0 替换；bge Xenova 来源确定 |
| T1c electron-builder 跑通 | ⬜ 待打包机 | 沙箱 EPERM（app-builder.exe spawn pipe 被拦），须脱离沙箱执行 |
| T2 M4/M5 物理链路 | ⬜ | 后置，下一阶段 |
