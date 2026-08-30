"""M2-B 情感状态机（mood / intimacy）—— 无 LLM，纯标准库。

事件驱动的心情值 ``mood`` + 亲密度 ``intimacy`` 两变量，随时间缓慢衰减 + 回归机制
（有下限、不归零）。数值**仅内部使用**：对外只显形态（星云连续参数 hue / brightness /
flow_speed），**禁止**经 ``get_visual_state()`` 暴露 mood / intimacy 数值（架构红线，
由 ``get_visual_state()`` 的键集合断言锁死）。

数值域：mood / intimacy 均钳制在 ``[MIN, MAX]``，其中 ``MOOD_MIN`` / ``INTIMACY_MIN``
为**回归下限**（>0，单调衰减永不归零）。事件增减整数化；衰减速率按天、向下取整。

线程安全：全部公共读写经模块级 ``_lock`` 保护（内存态单例）。``AffectState`` 不可变
（frozen），事件 / 衰减以「读-改-写」返回新实例，避免并发读-改-写丢更新。

持久化接口：本模块只维护**内存态**；持久化由 M1 画像层承接（事件
``memory.profile_updated`` / ``affect.updated``），本模块仅预留 ``last_interaction``
（时间戳）等快照字段，不直接调用 M1 内部函数、不硬 import 未落地模块。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# 数值域
# ---------------------------------------------------------------------------
MOOD_DEFAULT = 50   # 冷启动心情（"刚认识的好感"）
INTIMACY_DEFAULT = 20  # 冷启动亲密度

MOOD_MIN = 10        # 心情回归下限（>0，不归零）
MOOD_MAX = 100
INTIMACY_MIN = 5     # 亲密度回归下限（>0，不归零）
INTIMACY_MAX = 100

# 衰减速率（每天）——"衰减缓慢"，亲密度比心情更缓
MOOD_DECAY_PER_DAY = 0.5
INTIMACY_DECAY_PER_DAY = 0.2

# ---------------------------------------------------------------------------
# 事件效果表（§4.3：夸赞+2 / 骂-3 / 深夜长聊+1 / 连续 3 天无互动-1）
# ---------------------------------------------------------------------------
EVENT_PRAISE = "praise"
EVENT_SCOLD = "scold"
EVENT_LATE_NIGHT_TALK = "late_night_talk"
EVENT_NO_INTERACTION_3D = "no_interaction_3d"

EVENT_EFFECTS: dict[str, dict[str, int]] = {
    EVENT_PRAISE: {"mood": 2, "intimacy": 0},            # 夸赞 → 心情+
    EVENT_SCOLD: {"mood": -3, "intimacy": 0},            # 骂   → 心情-
    EVENT_LATE_NIGHT_TALK: {"mood": 1, "intimacy": 1},   # 深夜长聊 → 心情微升+亲密度累积
    EVENT_NO_INTERACTION_3D: {"mood": -1, "intimacy": -1},  # 连续 3 天无互动 → 缓慢回落
}

# ---------------------------------------------------------------------------
# AffectState：不可变 dataclass（事件驱动返回新实例）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AffectState:
    mood: int = MOOD_DEFAULT
    intimacy: int = INTIMACY_DEFAULT
    last_interaction: Optional[float] = None  # epoch 秒；None=从未互动


# 内存态单例（M1 画像层持久化前的运行时缓存）
_state = AffectState()
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _norm_event(event: object) -> str:
    return str(event or "").strip().lower()


# ---------------------------------------------------------------------------
# 公共接口
# ---------------------------------------------------------------------------
def apply_event(event: str) -> AffectState:
    """应用一个情感事件并返回更新后的状态。

    已知事件 → 按 ``EVENT_EFFECTS`` 增减并刷新 ``last_interaction``；
    未知 / 空事件 → 幂等返回当前状态（不视为互动，不刷新时间戳）。
    """
    global _state
    name = _norm_event(event)
    with _lock:
        delta = EVENT_EFFECTS.get(name)
        if delta is None:
            return _state
        mood = _clamp(_state.mood + delta["mood"], MOOD_MIN, MOOD_MAX)
        intimacy = _clamp(_state.intimacy + delta["intimacy"], INTIMACY_MIN, INTIMACY_MAX)
        _state = AffectState(
            mood=int(mood),
            intimacy=int(intimacy),
            last_interaction=time.time(),
        )
        return _state


def decay(days_passed: int) -> AffectState:
    """按无互动天数缓慢衰减，并钳制到回归下限（不归零）。

    衰减只降 mood / intimacy，**不**刷新 ``last_interaction``（无互动即无接触）。
    非正天数（含负值）视为 0 天，维持现状。
    """
    global _state
    days = max(0.0, float(days_passed or 0))
    with _lock:
        mood = _clamp(int(_state.mood - MOOD_DECAY_PER_DAY * days), MOOD_MIN, MOOD_MAX)
        intimacy = _clamp(int(_state.intimacy - INTIMACY_DECAY_PER_DAY * days), INTIMACY_MIN, INTIMACY_MAX)
        _state = AffectState(
            mood=int(mood),
            intimacy=int(intimacy),
            last_interaction=_state.last_interaction,
        )
        return _state


def get_visual_state() -> dict:
    """返回只显形态（星云连续参数），**绝不包含 mood / intimacy 数值**。

    形态与情感正相关单调映射（对外不暴露具体数值）：
    - ``hue``       ：随心情由冷（青蓝）向暖（橙红）过渡；
    - ``brightness``：随心情升高而变亮；
    - ``flow_speed``：随亲密度升高而更沉稳（缓速）。
    """
    with _lock:
        m = _state.mood
        i = _state.intimacy

    hue = _clamp(220.0 - (m - MOOD_MIN) * 2.0, 0.0, 360.0)
    brightness = _clamp((m - MOOD_MIN) / (MOOD_MAX - MOOD_MIN), 0.0, 1.0)
    flow_speed = _clamp(
        1.0 - (i - INTIMACY_MIN) / (INTIMACY_MAX - INTIMACY_MIN) * 0.5,
        0.0,
        1.0,
    )
    # 只返回形态键 —— 结构上不携带 mood/intimacy（红线）
    return {"hue": hue, "brightness": brightness, "flow_speed": flow_speed}


def get_state() -> AffectState:
    """读取当前内存态（快照副本）。"""
    with _lock:
        return _state


def reset() -> AffectState:
    """重置为冷启动（测试 / 会话重置用）。"""
    global _state
    with _lock:
        _state = AffectState()
        return _state
