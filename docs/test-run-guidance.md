# 小二（Xiao）· 本机试用测试指引 + 日志方案

> 版本 v1.0 ｜ 2026-09-01 ｜ 本机试用期专用（未发布）
> 目标：试用期间记录足够、必要、可分析的日志，快速定位问题；不发布、不 push。

## 一、日志现状盘点（缺口）

| 日志层 | 机制 | 落盘 | 打包态（Xiao.exe）可见 |
|---|---|---|---|
| 模块级日志（唤醒/ASR/TTS/LLM/事件/网关告警） | `logging.getLogger(__name__)` | ❌ 无 handler，走 stderr | ❌ **Electron `stdio:'ignore'` 全部丢弃** |
| 路由决策 | `logs/routes.jsonl` | ✅ | ✅ |
| 任务编排 | `logs/tasks.json` | ✅ | ✅ |
| 审计回放 | `logs/audit/`（append-only fact plane） | ✅ | ✅ |
| 权限延迟 | `logs/deferred.json` | ✅ | ✅ |
| Electron 主进程（后端拉起/退出/错误） | 无 | ❌ | ❌ |
| 前端交互日志（pushLog/console） | 内存态 | ❌ | ❌ |

**核心缺口**：Python `logging` 全链路告警在打包态**全部丢失**——这是试用期最需要、却恰好没记录的日志。

## 二、日志增强方案（最小必要，待实施）

> 三个改动点，均走相对路径 + config 开关，不硬编码、不影响开发态。实施后重新 `npm run dist` 打包即可。

### A. 后端文件日志（必做）

`config.yaml` 增 `logging` 段：

```yaml
logging:
  level: INFO          # DEBUG|INFO|WARNING|ERROR
  file: logs/xiao.log  # 相对 ROOT，轮转 5MB×3
```

`run.py` 在 `import config` 后、`uvicorn.run` 前挂一个轮转 FileHandler：

```python
import logging
from logging.handlers import RotatingFileHandler
from backend.config import ROOT, config

_log_path = ROOT / str(config.get("logging.file", "logs/xiao.log"))
_log_path.parent.mkdir(parents=True, exist_ok=True)
_h = RotatingFileHandler(_log_path, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
logging.getLogger().setLevel(str(config.get("logging.level", "INFO")).upper())
logging.getLogger().addHandler(_h)
```

> 覆盖范围：`backend/asr`、`audio/wake*`、`tts/*`、`llm`、`gateway`、`m3/*`、`event_bus` 等所有 `logging.getLogger(__name__)` 的告警/错误，打包态从 stderr 盲区转为落盘。

### B. Electron 主进程日志（建议）

`desktop/main.js` 加最小 `elog()`，在「启动 / 后端拉起 / 后端退出 / 单实例冲突 / 错误」处追加 `logs/electron.log`：

```js
const elog = (m) => { try { fs.appendFileSync(path.join(ROOT, 'logs', 'electron.log'), `[${new Date().toISOString()}] ${m}\n`) } catch {} }
```

挂点：`bootstrap()`、`startBackend()`、`backendProc.on('exit')`、`findPython()` 失败分支、`app.quit`。用于定位「后端没拉起/闪退」这类主进程问题。

### C. 前端 console 转发（可选）

`main.js` 里监听 `webContents` 的 `console-message`，把前端 `console.error/warn` 追加到 `electron.log`（不改前端代码即可回溯前端报错）。

## 三、本机试用测试清单

> 每项记录：步骤 → 预期 → 实际 → 关键日志（`logs/xiao.log` 时间戳片段 / `electron.log` / 截图）。

| # | 场景 | 操作 | 预期 | 关键日志关键词 |
|---|---|---|---|---|
| 1 | 首启向导 | 首启走 6 步（领 Key→连通测试→选大脑→测麦克风→完成） | 保存后进主界面，重开不再弹 | `config`、`provider_test`、`mic/echo` |
| 2 | 唤醒+对话 | 说「小二」→ 说话 → 看识别+回复 | 唤醒 chime、识别正确、回复播报 | `wake`、`asr`、`llm`、`tts` |
| 3 | 无网播报降级 | 断网说一句话 | Piper 本地合成系统提示，不崩 | `tts.piper`、`edge_tts` 降级告警 |
| 4 | 存储满弹窗 | 设置里调低 `memory.storage_budget_mb` 触发 | 弹「记忆存储将满」三按钮可用 | `maintenance`、`storage_threshold` |
| 5 | 成长回顾 | 积累若干成长记录后点「回顾」 | 三栏（你的/小二的/咱们的）分列 | `recall`、`m6` |
| 6 | 跨会话记忆 | 说「我叫 XX」→ 重启 → 问「我叫什么」 | 记住并答出 | `memv1`、`consolidate`、`persona` |
| 7 | 关机/休眠 | 说「退出程序」→ 确认 | 播报结束语后退出 | `shutdown`、`app_shutdown` |
| 8 | 任务/权限 | 触发一个需审批的联网/写文件任务 | 弹权限确认，允许/拒绝 | `perms`、`approval`、`audit` |

## 四、日志收集与分析

**收集**：每轮测试结束后，把 `logs/` 目录（`xiao.log`、`electron.log`、`routes.jsonl`、`audit/`、`tasks.json`）按问题打包留档；`logs/` 已在 `.gitignore`，不会误提交。

**分析定位**：

| 现象 | 先看 | 再 grep |
|---|---|---|
| 后端没起来/闪退 | `logs/electron.log` | `spawn`、`exit`、`ModuleNotFound` |
| 唤醒无反应 | `logs/xiao.log` | `wake`、`sherpa`、`silero` |
| 识别错/漏 | `logs/xiao.log` | `asr`、`partial`、`final` |
| 回复没声音 | `logs/xiao.log` | `tts`、`piper`、`qwen_rt`、`edge` |
| 记不住 | `logs/xiao.log` + `memv1/` 数据 | `memv1`、`consolidate`、`persona` |
| 路由乱（该聊天却执行） | `logs/routes.jsonl` | 判定结果字段 |
| 权限/审计异常 | `logs/audit/` | fact plane 事件流 |

## 五、问题上报格式（供老板汇总）

```
【场景】#N 唤醒+对话
【步骤】说「小二」→ 说「今天天气」
【预期】识别并回复
【实际】唤醒 chime 响但无识别
【日志】logs/xiao.log 第 N 行 asr 相关片段（附时间戳）
【复现】必现 / 偶发（N 次中 M 次）
```

## 六、实施顺序（待老板确认）

1. 实施 §二 的 A（必做）+ B（建议），C 可选。
2. 跑 `npm run dist` 重打包（本机 `signAndEditExecutable=false`，图标/签名暂不管）。
3. 按 §三 清单逐项试用，按 §四 收集日志，按 §五 上报。
