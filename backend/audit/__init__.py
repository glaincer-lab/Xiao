"""backend.audit · 可审计回放层（T8 · P3 增强，借鉴 HomeRail「可审计回放」）。

设计思想受 [xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）启发，
自研实现：把 bridge 的 event_sink 事件（tool/call、tool/result、assistant/chunk、
assistant/message、turn/end）追加式持久化为 run 级记录（append-only fact plane），
支持按 run_id 回放时间线（replay）与对 tool/result 做质量打点（scorecard）。
每个 run 独立记录目录（run 级工作区隔离，与 backend/tasks.py 的 logs/ 约定对齐）。
复用现有事件流，不改桥的核心解析；只读追加，不破坏既有记忆/画像。
不与 HomeRail 代码混淆：命名全套 backend/audit/ + xiao_ 前缀。
"""

from __future__ import annotations

from backend.audit.xiao_fact_plane import XiaoFact, XiaoFactPlane
from backend.audit.xiao_replay import XiaoReplay
from backend.audit.xiao_scorecard import XiaoScorecard, turn_end_ok
from backend.audit.xiao_audit import (
    FACT_EVENT_TYPES,
    XiaoAuditor,
    build_auditor,
)

__all__ = [
    "XiaoFact",
    "XiaoFactPlane",
    "XiaoReplay",
    "XiaoScorecard",
    "turn_end_ok",
    "XiaoAuditor",
    "build_auditor",
    "FACT_EVENT_TYPES",
]
