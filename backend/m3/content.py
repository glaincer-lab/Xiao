"""M3-M2 心跳内容源 + 质量门（backend/m3/content.py）。

实现 M3-proactive.md §4.2 心跳「内容质量门（无素材宁可不说；生成后自评分）」。
首版**不接 M1 检索**：默认内容源 build_candidate() 返回 None（零投递）；真实内容待
接入 M1 检索时替换，早期/测试由调用方（HeartbeatEngine）注入 StubContent 或自定义源。

候选结构（供 M3-M1 notify.process() 消费，字段与之保持对齐）：
    {
        "类型": str,                      # 候选类型（如 "心跳"）
        "内容草案": str,                   # 生成后待投递文案
        "特征": {4 个四维原始分},           # 供 score_candidate 归一化（urgency/actionability/relationship/freshness）
    }

边界（写死）：本包只做「有料才开口」的质量门，不生成心理标签、不发布任何事件；
内容源注入而非硬编码 M1 检索。

仅供标准库；MIT。
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Callable, Mapping, Protocol

# 质量门最低素材分：至少一个四维维度达到此分才视为「有料」（无素材宁可不说）
MIN_MATERIAL_SCORE: float = 0.6

# 四维维度名（与 score.DIMS 对齐；供质量门扫描）
_DIMS: tuple[str, ...] = ("urgency", "actionability", "relationship", "freshness")


class ContentSource(Protocol):
    """内容源协议：给定时刻与上下文，产出候选 dict 或 None（无素材）。"""

    def build_candidate(
        self, now: _dt.datetime, ctx: Mapping[str, Any]
    ) -> Mapping[str, Any] | None: ...


def build_candidate(
    now: _dt.datetime, ctx: Mapping[str, Any]
) -> None:
    """默认内容源：首版不接 M1 检索 → 无素材，返回 None（零投递）。

    真实实现待后续接入 M1 记忆检索；此处保持最小、默认保守（宁可不说）。
    """
    return None


def quality_gate(candidate: Mapping[str, Any] | None) -> bool:
    """生成后自评分质量门：无素材/无料 → 不投递（宁可不说）。

    规则（写死，首版）：
        - candidate 非空；
        - 内容草案非空（有值得说的话）；
        - 至少一个四维特征维度 >= MIN_MATERIAL_SCORE（有料，非空话）。
    返回 True 表示通过质量门、可交由 process() 消费。
    """
    if not candidate:
        return False
    draft = str(candidate.get("内容草案", "")).strip()
    if not draft:
        return False
    features = candidate.get("特征", candidate.get("四维分", {}))
    if not isinstance(features, Mapping):
        return False
    # 素材分以"某维度有内容"为准（宁可漏说，不可空说）
    return any(float(features.get(k, 0.0)) >= MIN_MATERIAL_SCORE for k in _DIMS)


class StubContent:
    """可配置内容源（测试/早期注入）：预设一次返回 preset（可 callable 或常量）。

    - preset=None：模拟无素材（零投递）；
    - preset=候选 dict：每次返回该候选；
    - preset=callable(now, ctx)->dict|None：动态生成。
    records 记录每次调用的 (now, ctx)，便于测试断言上下文。
    """

    def __init__(
        self,
        preset: Mapping[str, Any] | Callable[[_dt.datetime, Mapping], Mapping | None] | None = None,
    ) -> None:
        self.preset = preset
        self.records: list[tuple[_dt.datetime, dict]] = []

    def build_candidate(self, now: _dt.datetime, ctx: Mapping[str, Any]) -> Mapping[str, Any] | None:
        self.records.append((now, dict(ctx)))
        if callable(self.preset):
            return self.preset(now, ctx)
        return self.preset
