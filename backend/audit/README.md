# backend/audit · 可审计回放层（T8 · P3 增强）

> 设计思想受 [xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）启发。
> 本模块为小二（Xiao）**自研实现**（Python），不复制 HomeRail 代码；借鉴其
> 「append-only Activity fact plane + replay + scorecard + run 级工作区隔离」思想，
> 数据结构、命名、事件机制均为自有。

> 边界：**复用现有事件流**——订阅 bridge 的 event_sink（tool/call、tool/result、
> assistant/chunk、assistant/message、turn/end），追加式持久化为 run 级记录；
> **不改 bridge 的核心解析**，只读追加，不破坏既有记忆/画像，不改 tasks.py 核心。

## 定位

给 DSH 长任务（web 桥）提供「可审计回放」能力：每次 run（任务轮）的桥事件
被追加式落盘，事后可按 run_id 重放时间线、对 tool/result 做质量打点。

主要解决三件事：

1. **审计**：DSH 到底调用了哪些工具、成功/失败、助手说了什么，逐条可回查；
2. **复盘**：按 run_id 重放成先后有序的时间线（流式 chunk 合并为完整助手消息）；
3. **质量**：对 run 的 tool/result 做打点，产出 0-100 质量分与错误率等指标。

## 接入（由装配层 main.py 注入）

bridge 的 event_sink 在 main.py 里被 `_bridge_sink` 包装：前端上屏事件
（work_step/dsh_chunk）仍照常 emit，原始事件（五类）另喂 auditor.handle_event
只读追加。bridge 侧新增 `_emit_raw` 把原始事件连同 run_id 上抛——仅追加式上抛，
不改任何解析逻辑（call_names/chunks/last_message 维持原样）。

```python
from backend.audit import build_auditor
auditor = build_auditor()          # 装配层持有
auditor.handle_event("tool/call", {"run_id": "ab12cd34", "name": "clock"})
timeline = auditor.replay("ab12cd34")           # 重放时间线
card = auditor.scorecard("ab12cd34")            # 质量打点
```

## run 级工作区隔离

每个 run 一个独立记录目录，与 backend/tasks.py 的 logs/ 约定对齐（run_id 与
task_id 同为 8 位 hex），基目录来自 config（audit.log_dir，默认 logs/audit，
基于 ROOT 解析，不写死本机绝对路径）：

```
<base_dir>/<run_id>/events.jsonl    ← append-only 事实日志（唯一事实来源）
<base_dir>/<run_id>/scorecard.json  ← 打分结果（由 scorecard 写入，非事实源）
```

## 模块文件

| 文件 | 职责 |
|---|---|
| xiao_fact_plane.py | XiaoFactPlane：run 级追加式事实平面（append-only JSONL，逐 run 独立目录） |
| xiao_replay.py | XiaoReplay：按 run_id 重放生成时间线（chunk 合并）+ 人类可读渲染 |
| xiao_scorecard.py | XiaoScorecard：对 run 的 tool/result 做质量打点 |
| xiao_audit.py | XiaoAuditor：bridge event_sink 订阅方，只读追加 + replay/scorecard 便捷入口 |

## 配置

config.yaml：

```yaml
audit:
  enabled: true
  log_dir: logs/audit
```

enabled 供装配层判断是否构建 auditor（默认开启）；即使关闭，默认基目录兜底仍在。

## 设计书

详见 _audit/执行方案-2026-08-30.md T8。

## 启发来源

[xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）
