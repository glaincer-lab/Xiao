"""M3-M1 预算制候选消费：四维打分（backend/m3/score.py）。

实现 M3-proactive.md §3 的 four_dim_scores 与 §4.1 打分排名。纯函数、无副作用，
便于单测与在 notify.py 中复用。

权重（写死，来自 §3，和必须为 1.0）：
    urgency 0.35 / actionability 0.30 / relationship 0.25 / freshness 0.10
各维归一化到 [0,1]，总分 = 加权和 ∈ [0,1]。

另提供与豁免穿透相关的两个判据：
    - is_relationship_boom()：关系价值单项爆表（生日/纪念日）→ §4.1 豁免穿透、不占额度
    - relationship_decay()：关系价值随时间的单项衰减（纪念日/哀伤节奏过期降权）

仅供标准库；MIT。
"""
from __future__ import annotations

from typing import Mapping, Any

# 四维权重（写死，来自 M3-proactive.md §3；权重和必须为 1.0）
WEIGHTS: dict[str, float] = {
    "urgency": 0.35,
    "actionability": 0.30,
    "relationship": 0.25,
    "freshness": 0.10,
}
DIMS: tuple[str, ...] = ("urgency", "actionability", "relationship", "freshness")

# 打分后进入消费流程的总分阈值（§4.1：>0.6 才消费；≤0.6 静默丢弃）
TOTAL_THRESHOLD: float = 0.6

# 关系价值单项爆表阈值（§4.1 生日/纪念日豁免穿透；首版可调）
RELATIONSHIP_BOOM_THRESHOLD: float = 0.9


def normalize(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """把原始分数线性归一化并钳制到 [low, high]（当低=0、高=1 时结果 ∈ [0,1]）。"""
    value = float(value)
    if high <= low:
        return 0.0
    clamped = max(low, min(high, value))
    return (clamped - low) / (high - low)


def total_score(dims: Mapping[str, float], weights: Mapping[str, float] | None = None) -> float:
    """四维加权总分（各维视为已归一化到 [0,1]），结果 ∈ [0,1]。"""
    weights = weights or WEIGHTS
    return sum(float(dims.get(k, 0.0)) * float(weights.get(k, 0.0)) for k in DIMS)


def score_candidate(features: Mapping[str, Any]) -> dict[str, float]:
    """输入候选原始特征 → 各维归一化分 + 加权总分。

    features: 各维原始值（可为超范围，自动钳制到 [0,1]）。
    Returns: {"urgency","actionability","relationship","freshness","total"}。
    """
    dims: dict[str, float] = {k: normalize(features.get(k, 0.0)) for k in DIMS}
    dims["total"] = round(total_score(dims), 4)
    return dims


def relationship_decay(value: float, elapsed_days: float, half_life_days: float = 7.0) -> float:
    """关系价值单项衰减：随时间按半衰期指数衰减（纪念日/哀伤节奏过期降权）。"""
    value = max(0.0, min(1.0, float(value)))
    if half_life_days <= 0:
        return value
    return round(
        max(0.0, min(1.0, value * (0.5 ** (float(elapsed_days) / half_life_days)))), 4
    )


def is_relationship_boom(
    dims: Mapping[str, float], threshold: float = RELATIONSHIP_BOOM_THRESHOLD
) -> bool:
    """关系价值是否单项爆表（>=threshold），用于 §4.1 豁免穿透、不占额度。"""
    return float(dims.get("relationship", 0.0)) >= float(threshold)
