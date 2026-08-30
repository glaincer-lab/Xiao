# M0 出网网关 · 语义消歧 接入与安装指引

> 模块：M0 出网安全网关 · 语义消歧（可选增强）
> 目标：让「密码学 / 密钥管理」这类**正常技术词**不被黑词表误拦，
>       同时真实敏感数据与自伤内容**绝不**被放行。
> 面向：第三使用者（拿到包就能装、就能用）+ 后续 A5 编排开发者。

---

## 1. 它解决什么

黑词表（local_only_keywords）是**确定性硬闸**，开箱即用。但纯正则
会把「学习**密码**学」「**密钥**管理」这类技术词误判为敏感 ——
这正是 M0-core 开放问题 2 的「误拦截」。

语义消歧是用**本地小模型**对「已命中黑词的文本」再判断一次语境：

- **技术/安全语境**（密码学、重置密码、CA 证书、密钥对…）→ 放行出网；
- **真实敏感数据 / 自伤**（我的密码是 123、我不想活了…）→ 本机留；
- **模型缺失或拿不准** → 交还规则判断（allow_words），**绝不裸奔出网**。

## 2. 角色与边界（重要）

judge_context(text, hit_word) 返回 "safe" | "block" | "unknown"：

- 它是**放行器**，不是拦截器：拦截仍由黑词表硬闸负责；
- **自伤红线**（自杀 / 不想活了）恒为 block，任何语境都不放行；
- unknown 时由 A5 编排**回退到 allow_words 规则**：规则命中→safe，否则→block。
  即模型没装 = 纯规则，不会因为缺模型而崩、也不会误放。

## 3. 配置（compliance.yaml，契约字段只增不改）

```yaml
compliance_gateway:
  semantic:
    enabled: true               # 模型在则参与消歧；模型缺失自动走规则兜底
    backend: "onnx"             # 推理后端（当前支持 onnxruntime）
    model_dir: "models/gateway-semantic"   # 相对项目根
    model_name: "bge-small-zh-v1.5"        # 预装默认；可换 bge-large / reranker
    threshold: 0.62             # 技术语境相似度阈值，>= 判定 safe（接入后按需调）
```

> threshold 与 anchors（内置技术词集）在接入后按自家语料微调；过低会误放，
> 过高会恢复误拦。起步按 0.62，接一个真实句子集回归一次。

## 4. 安装（预装模型，开箱即用）

两种渠道：

**A. 发布包预置（推荐，开箱即用）**
发布时把 models/gateway-semantic/bge-small-zh-v1.5/（含 model.onnx +
tokenizer.json + vocab.txt）打进包，用户拿到即可用，enabled 置 true。

**B. 脚本下载（在线渠道）**
```powershell
python scripts/install_gateway_model.py
```
脚本会从 HuggingFace（BAAI/bge-small-zh-v1.5）拉取 tokenizer 配置文件；
ONNX 需要单独提供（见脚本内指引，或 --onnx 路径带入）。
网络不通时脚本会给「手动放置」路径，不会抛裸堆栈。

前置依赖：onnxruntime（已在 requirements）、tokenizers（可选，未装则语义
消歧自动降级为规则——不影响正常使用）、numpy（已在 requirements）。

## 5. 升级到更强的模型（按需，非默认）

当默认的 bge-small 消歧不够用时，换更重的本地模型，**只改配置不动代码**：

| 档位 | model_name | 说明 |
|---|---|---|
| 默认 | bge-small-zh-v1.5 | 几十 MB，CPU 毫秒级，够用 |
| 进阶 | bge-large-zh-v1.5 | 更大更准，体积增到几百 MB |
| 重排 | bge-reranker-v2-m3 | 相关性重排最强，偏重 |

步骤：把对应模型的 model.onnx + tokenizer 放入
models/gateway-semantic/<model_name>/，再把 semantic.model_name 改成它，重启即可。
模型体积较大时按 AGENTS.md 标注「按需安装」。

## 6. 接入（A5 编排层）

```python
from backend.gateway.blocklist import detect_blocked
from backend.gateway.semantic_filter import judge_context, is_semantic_available

hit = detect_blocked(text, cfg["compliance_gateway"]["local_only_keywords"])
if hit is None:
    return  # 正常出网

if is_semantic_available(cfg):
    verdict = judge_context(text, hit, cfg)
    if verdict == "safe":
        return  # 技术语境，放行
    if verdict == "block":
        return  # 本机留（静默拦截）
    # verdict == "unknown"：交给规则 allow_words
# 规则兜底：allow_words 覆盖则放行，否则本机留
if _allowed_by_rule(text, hit, cfg):
    return
```

其中的 _allowed_by_rule 即 A2 的豁免逻辑：命中位置落在 allow_words 内 → 放行。

## 7. 失败礼仪

模型缺失/推理失败时 judge_context 返回 unknown、semantic_error() 给一句人话原因
（可供设置页展示），不抛裸堆栈。自伤红线恒拦，静默、不惊扰。

## 8. 当前限制（如实说明）

- 官方 BAAI/bge-small-zh-v1.5 仓库默认**不含 ONNX**，需导出或提供
  （脚本内给 optimum-cli export onnx 指引）；
- threshold / anchors 需在真实语料上回归校准，以上为起点经验值；
- 语义消歧是**增强**不是必需：模型缺失时网关自动回到纯规则，冷启动零依赖可用。

---

MIT · 未复制 GPL/AGPL 代码 · 全程本地离线，符合「数据不上网」。
