"""M3-M4 纪念日豁免 + 画像异常侦测（backend/m3/anniversary.py）。

实现 M3-proactive.md sec 4.5 数字纪念日（v4 终裁）与画像异常侦测纪律：
    - 彻底废除纯计数型（第 N 句话）——不再生成该类候选；
    - 保留两类穿透豁免：1 里程碑能力见证 2 正向极值见证（恢复/坚持/成就 only）
      → is_witness_exempt(date)；生成候选 relationship=0.9 爆表 → M3-M1 notify 豁免不占额度；
    - 画像异常侦测（负向）：读 M1 惯例画像 habit_profile（只读）→ 检测负向异常（打破惯例）
      → 标记「不主动开场」，仅响应时 Context 增强 / 偏离惯例确认（仅留行为语义，不实现对话干预）；
      负向异常**永不主动开场**（不生成候选/不投递）。

事件契约（sec 6 / EVENT_REGISTRY sec 一）：本模块只**订阅** memory.profile_updated（已登记），
**不新增事件、不改 EVENT_REGISTRY、不改 backend/event_bus.py 的 EVENT_TYPES 白名单**。
（schedule.anniversary 由 M3-M3 event_trigger 消费，本模块不重复订阅以避重复消费。）

边界（写死）：
    - 跨模块走 event_bus（bus.on/emit），禁止直调其他模块核心函数；读 M1 只读快照不写；
    - 纪念日/画像数据源未接真实前用可注入 stub；数据只判定，不存原始内容；
      不推断/不存心理标签；
    - 主动类只建议不执行；消费已由 M3-M1 notify.process() 负责（本模块不重复实现预算）。

仅供标准库；MIT。
"""
from __future__ import annotations

import logging
from functools import partial
from typing import Any, Callable, Mapping

from backend.m3.score import RELATIONSHIP_BOOM_THRESHOLD

logger = logging.getLogger("m3.anniversary")

# ---- 见证类别（写死）：只有 milestone / positive 两类穿透豁免；count 纯计数型废除 ----
WITNESS_MILESTONE = "milestone"   # 里程碑能力见证（M6.1 双源入册）："第一次不问你就调好室温"
WITNESS_POSITIVE = "positive"     # 正向极值见证（恢复/坚持/成就 only）："连续三周周末休息"
WITNESS_COUNT = "count"           # 纯计数型（第 N 句话）→ 废除

# 穿透豁免类别集合（两类）
WITNESS_EXEMPT_TYPES: frozenset[str] = frozenset({WITNESS_MILESTONE, WITNESS_POSITIVE})

# 实际可生成候选的两类（仅两类）；其它（含 count / 未知）不生成豁免候选
CANDIDATE_WITNESS_TYPES: frozenset[str] = WITNESS_EXEMPT_TYPES

# 负向异常（打破惯例）信号 / 状态
NEGATIVE_ANOMALY_STATUSES: tuple[str, ...] = ("missed", "broken")
NEGATIVE_ANOMALY_SIGNALS: tuple[str, ...] = (
    "habit_break",
    "schedule_deviation",
    "routine_miss",
)

# 负向异常行为语义（写死，仅留行为标记，不实现对话干预）：
#   负向异常 → 永不主动开场；仅①响应时 Context 增强 ②偏离惯例确认。
ANOMALY_BEHAVIOR: str = "context_enhance_only"

# 订阅的已登记事件（只订阅不新增）
SUBSCRIBED_EVENTS: tuple[str, ...] = ("memory.profile_updated",)


def _default_bus():
    from backend.event_bus import bus
    return bus


def _witness_type(entry: Mapping[str, Any]) -> str:
    """读取纪念日条目的见证类别（支持 见证类别 / witness_type 两种键）。"""
    return str(entry.get("见证类别", entry.get("witness_type", "")))


def is_witness_exempt(entry: Mapping[str, Any]) -> bool:
    """穿透豁免判定（sec 4.5 终裁）：里程碑能力见证 / 正向极值见证 → 豁免；其余（含纯计数型）不豁免。"""
    return _witness_type(entry) in WITNESS_EXEMPT_TYPES


def is_count_based(entry: Mapping[str, Any]) -> bool:
    """纯计数型（第 N 句话）标记 → 已废除（不生成候选）。"""
    return _witness_type(entry) == WITNESS_COUNT


def is_negative_anomaly(observation: Mapping[str, Any] | None, profile: Mapping[str, Any] | None) -> bool:
    """画像异常（负向）判定：观察是否构成「打破惯例」。

    惯例画像（M1 habit_profile，只读）快照结构：{mode, rebuild_reason, habits:[{id,content,category,status,since}]}。
    观察(observation)结构：{habit_id, status} 或 {signal}。
    规则（写死，保守）：
        - observation 绑定惯例（habit_id）且该惯例本次状态为 missed/broken → 负向异常；
        - 惯例未在画像中但明确标记打破 → 保守判定负向；
        - 画像在重建模式（mode=rebuild）且 observation 带负向信号（habit_break/schedule_deviation/routine_miss）
          → 负向异常（重大转变敏感期）；
        - 其余 → 非负向。
    """
    if not observation:
        return False
    profile = profile or {}
    status = str(observation.get("status", ""))
    habit_id = str(observation.get("habit_id", ""))
    if habit_id:
        habits = profile.get("habits", [])
        habit = next(
            (h for h in habits if isinstance(h, Mapping) and str(h.get("id", "")) == habit_id),
            None,
        )
        # 惯例已入画像或被标记打破，一律按打破判定（保守）
        if habit is not None or status in NEGATIVE_ANOMALY_STATUSES:
            return status in NEGATIVE_ANOMALY_STATUSES
        return False
    # 无 habit_id：仅当画像在重建模式且观察带负向信号 → 负向异常
    if str(profile.get("mode", "normal")) == "rebuild" and str(profile.get("rebuild_reason", "")):
        return str(observation.get("signal", "")) in NEGATIVE_ANOMALY_SIGNALS
    return False


class AnniversaryEngine:
    """M3-M4 纪念日豁免 + 画像异常侦测引擎。

    依赖可用关键字注入（便于测试替身；缺省懒加载真实单例）：
        bus       事件总线实例（默认全局 bus）
        registry  纪念日登记源（提供 list_anniversaries(now)->list[entry]；首版可注入 stub）
        profile   M1 惯例画像源（提供 habit_profile()->dict；缺省无画像 → 不触发异常）
        now_fn    返回当前时刻（默认 datetime.now）
        config    配置字典（备用；无硬编码本机路径）
    """

    def __init__(
        self,
        bus: Any | None = None,
        registry: Any | None = None,
        profile: Any | None = None,
        now_fn: Callable[[], Any] | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self._bus = bus if bus is not None else _default_bus()
        self._registry = registry
        self._profile = profile
        self._now_fn = now_fn
        self._config = dict(config or {})
        self._negative_anomaly: bool = False
        self._profile_snapshot: dict[str, Any] = {}
        # 只读画像快照（M1 habit_profile 只读；无画像默认空）
        self._refresh_profile()
        # 订阅 memory.profile_updated（bus.on；事件名已在白名单，只订阅不新增）
        self._unsubs: list[Callable[[], None]] = []
        for evt in SUBSCRIBED_EVENTS:
            self._unsubs.append(self._bus.on(evt, partial(self._on_event, evt)))
        # 负向异常行为语义标记（只读，不实现对话干预）
        self.anomaly_behavior: str = ANOMALY_BEHAVIOR

    # =================================================================
    # 事件入口（bus.on handler）
    # =================================================================
    def _on_event(self, event_type: str, payload: Any = None) -> None:
        if event_type == "memory.profile_updated":
            self._refresh_profile()

    def _refresh_profile(self) -> None:
        """只读 M1 惯例画像快照（habit_profile()）；缺省无画像 → 空快照。"""
        p = self._profile
        if p is None:
            self._profile_snapshot = {}
            return
        try:
            if hasattr(p, "habit_profile"):
                res = p.habit_profile()
            else:
                res = p
        except Exception:  # noqa: BLE001
            logger.debug("画像源异常，视为无画像", exc_info=True)
            res = None
        self._profile_snapshot = dict(res or {})

    # =================================================================
    # 穿透豁免 / 候选生成（sec 4.5 终裁）
    # =================================================================
    def is_witness_exempt(self, entry: Mapping[str, Any]) -> bool:
        return is_witness_exempt(entry)

    def build_candidate(self, entry: Mapping[str, Any]) -> dict | None:
        """由纪念日登记生成候选；未满足前提时返回 None（不主动开场）。

        前提（写死）：
            - 负向异常期间 → 绝不主动开场（不生成候选/不投递）；
            - 废除纯计数型（第 N 句话）→ 不生成；
            - 仅里程碑能力见证 / 正向极值见证两类 → 生成 relationship=0.9 爆表候选
              （供 M3-M1 notify 豁免穿透、不占额度）。
        """
        if self._negative_anomaly:
            logger.info("[m3.anniversary] 负向异常期间，纪念日候选不主动开场")
            return None
        if not is_witness_exempt(entry):
            logger.info("[m3.anniversary] 非穿透豁免（含纯计数型），纪念日候选废除")
            return None
        evt = str(entry.get("事件", "") or entry.get("类型", ""))
        draft = str(entry.get("内容草案", "")).strip() or f"今天是「{evt}」纪念日，我记得。"
        return {
            "类型": "纪念日",
            "内容草案": draft,
            "特征": {"urgency": 0.6, "actionability": 0.5, "relationship": RELATIONSHIP_BOOM_THRESHOLD, "freshness": 0.7},
            "_agg_key": "anniversary",
            "_event_type": "schedule.anniversary",
            "_witness_type": _witness_type(entry),
        }

    def list_candidates(self, now: Any | None = None) -> list[dict]:
        """从纪念日登记源批量生成候选（过滤 None）；供未来接入 event_trigger / notify 批量消费。

        数据源 registry 未接真实前用可注入 stub（list_anniversaries(now)->list[entry]）。
        """
        if self._registry is None:
            return []
        now = now or self._now()
        try:
            if hasattr(self._registry, "list_anniversaries"):
                entries = list(self._registry.list_anniversaries(now) or [])
            else:
                entries = list(self._registry(now) or [])
        except Exception:  # noqa: BLE001
            logger.debug("纪念日登记源异常，视为无登记", exc_info=True)
            return []
        return [c for e in entries if (c := self.build_candidate(e)) is not None]

    # =================================================================
    # 画像异常侦测（负向）—— 永不主动开场
    # =================================================================
    def detect_negative_anomaly(self, observation: Mapping[str, Any] | None) -> bool:
        """检测负向异常（打破惯例）。命中 → 标记不主动开场。"""
        if is_negative_anomaly(observation, self._profile_snapshot):
            self._negative_anomaly = True
            logger.info("[m3.anniversary] 负向异常（惯例偏离），标记不主动开场")
            return True
        return False

    def clear_anomaly(self) -> None:
        """清除负向异常标记（应对随后用户响应/偏离确认后的恢复；正常不主动清除）。"""
        self._negative_anomaly = False

    @property
    def negative_anomaly(self) -> bool:
        """当前是否处于负向异常（不主动开场）状态。"""
        return self._negative_anomaly

    def should_open(self) -> bool:
        """是否允许主动开场；负向异常期间返回 False（永不主动开场）。"""
        return not self._negative_anomaly

    # =================================================================
    # 依赖 / 生命周期
    # =================================================================
    def _now(self) -> Any:
        if self._now_fn is not None:
            return self._now_fn()
        import datetime as _dt
        return _dt.datetime.now()

    def close(self) -> None:
        """取消全部订阅（运行时清理；通常不调用，引擎为长生命周期单例）。"""
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass
        self._unsubs.clear()


__all__ = [
    "SUBSCRIBED_EVENTS",
    "WITNESS_MILESTONE",
    "WITNESS_POSITIVE",
    "WITNESS_COUNT",
    "WITNESS_EXEMPT_TYPES",
    "CANDIDATE_WITNESS_TYPES",
    "NEGATIVE_ANOMALY_STATUSES",
    "NEGATIVE_ANOMALY_SIGNALS",
    "ANOMALY_BEHAVIOR",
    "is_witness_exempt",
    "is_count_based",
    "is_negative_anomaly",
    "AnniversaryEngine",
]
