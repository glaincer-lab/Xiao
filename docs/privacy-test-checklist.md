# 隐私合规与授权改造 · 本机测试清单

> 对应设计书 `docs/specs/privacy-authorization.md` ｜ 2026-09 ｜ 提交 316eb29..2af7c19

## 环境准备

| 项 | 操作 |
|---|---|
| 后端 | `.venv\Scripts\python.exe run.py`（端口 8123，可在 config.yaml `server.port` 改） |
| 前端 | `cd frontend && npm run dev` → 打开 http://localhost:5173 |
| 触发开箱向导 | 浏览器 F12 → Application → Local Storage → 删除 `xiao_onboarded` → 刷新 |
| API 直测 | 下方 PowerShell 片段均在项目根目录执行（`$B="http://127.0.0.1:8123"`） |

⚠️ 关键前提：`cloud_llm` 默认关 → 主对话开箱回退本地 Ollama。本机未装 Ollama 时对话会报连接失败，**这是预期行为**（最小化默认），测试云端对话需先在向导/面板授权。

---

## A. 开箱引导 6 步（UI 手测）

| # | 步骤 | 预期 |
|---|---|---|
| A1 | 删 localStorage 后刷新 | 弹出欢迎页，文案含「你的数据去哪儿」「每步都能跳过」 |
| A2 | 联网方式：点「自动检测」 | 显示 GPU 型号；检测不出则引导手选档位，**不虚构硬件** |
| A3 | 手选「8GB」 | 推荐⭐联网模式；选 16GB/24GB+ → 推荐本地；卡片列名是「须知」不是「代价」 |
| A4 | 选「联网」 | 展开 DeepSeek/通义领 Key 子步骤（链接可点、可填 Key、可连通测试） |
| A5 | 选「自定义」 | 展开听懂/脑子/看图/说话四项，每项有「隐私说明」🔗弹窗（收集什么/发给谁/存多久/如何撤回） |
| A6 | 第3步不勾选直接跳过 | 剪贴板+截屏保持关闭 |
| A7 | 第4步勾「火警」+手填一条 | 白名单保存这两条 |
| A8 | 第5步试麦克风 | 录 3 秒有回放、peak>0 |
| A9 | 完成页总结卡 | 内容与实际选择一致（如「语音发阿里云/对话走本地/剪贴板未开启/白名单 2 条」）；点保存不再弹向导 |
| A10 | 每步点跳过 | 全部跳过后仍能进入主界面，所有能力保持默认关 |

## B. 默认最小化（API 层）

```powershell
$B="http://127.0.0.1:8123"
# B1 十二项授权：除 guard_outbound=true 外全 false
(Invoke-RestMethod "$B/api/authorizations").authorizations
```

| # | 验证 | 预期 |
|---|---|---|
| B1 | 上述返回 | 12 个 key；cloud_asr/cloud_llm/cloud_vision/cloud_tts/clipboard_read/screen_capture 全 false，guard_outbound=true |
| B2 | 对小二说「读一下剪贴板」（未授权） | 回「读取剪贴板未开启：请在设置 → 隐私授权里开启后再说。」 |
| B3 | 说「截个屏」（未授权） | 回「截屏未开启…」提示 |
| B4 | 贴图发送（cloud_vision 未授权） | 回「当前没有开启图片输入…」提示，图片不上云 |

## C. 授权开闸后行为（面板 + 实测）

| # | 操作 | 预期 |
|---|---|---|
| C1 | 面板开 `cloud_asr`+`cloud_tts`（需 asr/tts 云端 Key） | 语音识别/播报走云，正常出字出声 |
| C2 | 开 `cloud_llm` + 已填 Key | 主对话走云（回云端答） |
| C3 | 关 `cloud_llm`（若装了 Ollama） | 对话回退本地 qwen3:8b，能答但稍慢 |
| C4 | 开 `clipboard_read` 后说「读剪贴板」 | 读出内容（截断 200 字内提示） |
| C5 | 开 `screen_capture` + 设置里开「语音操电脑」+「支持图片输入」+`cloud_vision` | 说「看看屏幕」→ 截图附给模型并描述 |
| C6 | `guard_outbound` 开关 | 关掉后出网不再脱敏（可用 E2 验证）；默认开 |

## D. 网关脱敏（E 组用命令直测，5 秒搞定）

```powershell
.venv\Scripts\python.exe -c "from backend.gateway.gateway import guard_outbound as g; print(g('我妈在家','t1')); print(g('我银行卡号发你','t2'))"
```

| # | 预期 |
|---|---|
| E1 | 「我妈」→ `('cloud_safe', 'User_Kinship_Mother在家')` 亲属词被占位符替换 |
| E2 | 「银行卡」→ `('blocked', 原文)` 黑词整条拦截、不出网 |

## E. 白名单热加载

| # | 操作 | 预期 |
|---|---|---|
| F1 | 面板「紧急穿透清单」加一条并保存 | `GET /api/authorizations` 立即反映新值 |
| F2 | 白名单清空再保存 | 立即为空，无需重启 |

## F. 删除入口（6 个端点逐一直测）

```powershell
$B="http://127.0.0.1:8123"
# 先造一条记忆：对小二说「记住我咖啡喜欢少冰」
Invoke-RestMethod "$B/api/memory/list"            # 记下 id
Invoke-RestMethod "$B/api/memory/delete" -Method Post -ContentType "application/json" -Body '{"id":"<id>"}'
# 区间删（快捷项由前端换算 ts）：删 30 天前
$ts=[DateTimeOffset]::Now.AddDays(-30).ToUnixTimeSeconds()
Invoke-RestMethod "$B/api/memory/delete_range" -Method Post -ContentType "application/json" -Body ('{"start_ts":0,"end_ts":'+$ts+'}')
Invoke-RestMethod "$B/api/audit/clear"      -Method Post   # 审计
Invoke-RestMethod "$B/api/persona/clear"    -Method Post   # 画像
Invoke-RestMethod "$B/api/memv4/clear"      -Method Post   # 会话原文
Invoke-RestMethod "$B/api/memv1/profile/clear" -Method Post # 向量画像（成长 P0 保留）
```

| # | 预期 |
|---|---|
| G1 | 单条删后再 list 少一条 |
| G2 | 区间删返回 removed 数，旧记忆清掉、新记忆保留 |
| G3-G6 | 各端点返回 `{"ok":true,...}`；对应文件（logs/memv4、persona、logs/audit、logs/memv1_profile.json）被清空/缩减；删除数据子区按钮同样可用 |

## G. 记忆容量

| # | 操作 | 预期 |
|---|---|---|
| H1 | `.venv\Scripts\python.exe -c "from backend.memory import DEFAULT_MAX_ENTRIES as M, DEFAULT_INJECT_LIMIT as I; print(M,I)"` | `500 30` |
| H2 | 连说 20+ 条「记住…」后问之前的事 | 能召回（注入上限 30） |

## H. 回归（每次测完跑一遍）

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests   # 预期 949 OK
cd frontend; npm run check; npm run build                # 预期 0 error
```

---

## 附：已知语义边界（测到时勿当 bug）

1. `cloud_llm` 关 + 未装 Ollama → 对话报连接失败：**预期**，先授权云或装本地模型。
2. 试听 TTS（设置面板「试听」）**不受** cloud_tts 授权拦截——有意为之（用户主动测试连通）。
3. 出网黑词命中时整条本机留，云端模型看不到该句，回复可能答非所问——脱敏预期行为。
