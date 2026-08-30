"""M3 主动引擎 · 预算制候选消费核心包（backend/m3）。

本阶段（M3-M1）只实现「预算制候选消费」：每日额度预算、四维打分、消费流程
（勿扰检查 + 事件发布）。订阅端（画像回写/仲裁）与 DORMANT 状态机本体
（backend/macro_state.py）留给后续包。本包只发布事件、不自增事件登记。

实现依据：docs/specs/M3-proactive.md（§3 数据模型 / §4.1 候选消费流程 / §6 事件契约）。
事件名 proactive.candidate 与 proactive.delivered 已登记 EVENT_REGISTRY 与
backend/event_bus.py 的 EVENT_TYPES 白名单，本包只作发布方、不新增登记。

仅供标准库；MIT。
"""
from backend.m3.budget import DEFAULT_DAILY_QUOTA, ProactiveBudget, QuotaExceededError
from backend.m3.notify import (
    DELIVERED,
    DROPPED_DND,
    DROPPED_DORMANT,
    DROPPED_FULLSCREEN,
    DROPPED_QUOTA_EXCEEDED,
    DROPPED_SCORE_LOW,
    ProactiveNotifier,
)
from backend.m3.score import (
    DIMS,
    RELATIONSHIP_BOOM_THRESHOLD,
    TOTAL_THRESHOLD,
    WEIGHTS,
    is_relationship_boom,
    normalize,
    relationship_decay,
    score_candidate,
    total_score,
)

__all__ = [
    # budget
    "ProactiveBudget",
    "QuotaExceededError",
    "DEFAULT_DAILY_QUOTA",
    # score
    "WEIGHTS",
    "DIMS",
    "TOTAL_THRESHOLD",
    "RELATIONSHIP_BOOM_THRESHOLD",
    "normalize",
    "score_candidate",
    "total_score",
    "relationship_decay",
    "is_relationship_boom",
    # notify
    "ProactiveNotifier",
    "DELIVERED",
    "DROPPED_DORMANT",
    "DROPPED_FULLSCREEN",
    "DROPPED_SCORE_LOW",
    "DROPPED_QUOTA_EXCEEDED",
    "DROPPED_DND",
]
