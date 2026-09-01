"""M3-M3 事件触发引擎（backend/m3/event_trigger.py）。

实现 M3-proactive.md §4.3 事件触发：
    事件源 → 相关性判定 → 紧急度分级 → 通知策略（立即/攒着/只亮灯）
      → 同类窗口聚合（防事件风暴，见 aggregate.py）→ 冷却检查
      → 生成候选 → 调 M3-M1 notify.process() 消费（复用 M3-M1，不重复实现预算）。

预判型触发（§4.3 / §8 开放问题 3）：只上线「天气 + 明日日程」两源 join →
「明早加衣提醒」；历史偏好 join 首版可 stub/省略。

哀伤节点（§3 / §7）：grief_schedule（3/7/30 天，M1.6 喂入）节点到点 → 触发哀伤候选，
走关系价值 / 穿透豁免判定（候选 relationship 爆表 → M3-M1 豁免不占额度）。

事件契约（§6 / EVENT_REGISTRY §一）：本引擎只**订阅** 6 个已有事件，
**不新增事件、不改 EVENT_REGISTRY、不改 event_bus.EVENT_TYPES 白名单**：
    schedule.anniversary / env.anomaly / device.state_changed /
    macro.state_changed / memory.profile_updated / affect.updated

边界（写死）：
    - 跨模块一律走 event_bus（bus.on/emit），禁止直调其他模块核心函数；
    - 主动类只建议不执行；消费已由 M3-M1 notify.process() 负责；
    - 未接真实数据源前用可注入 stub（天气/日程/内容）；数据只判定，不存原始内容；
    - 密钥走 env/config；相对路径；无硬编码本机绝对路径。

仅供标准库；MIT。
"""
from __future__ import annotations

import datetime as _dt
import logging
import time
from functools import partial
from typing import Any, Callable, Mapping

from backend.authorization import AuthorizationCenter
from backend.m3.aggregate import EventWindowAggregator
from backend.m3.score import RELATIONSHIP_BOOM_THRESHOLD, WEIGHTS, total_score

logger = logging.getLogger("m3.event_trigger")

# 订阅的 6 个已有事件（全部已在 EVENT_REGISTRY / event_bus.EVENT_TYPES 白名单登记）
SUBSCRIBED_EVENTS: tuple[str, ...] = (
    "schedule.anniversary",
    "env.anomaly",
    "device.state_changed",
    "macro.state_changed",
    "memory.profile_updated",
    "affect.updated",
)

# 紧急度分级（低/中/高）
URGENCY_LOW = "low"
URGENCY_MED = "med"
URGENCY_HIGH = "high"

# 通知策略：立即开口 / 攒着（先入 pending 缓冲，后 flush 批量投递）/ 只亮灯（不开口）
POLICY_IMMEDIATE = "immediate"
POLICY_ACCUMULATE = "accumulate"
POLICY_LIGHT = "light_only"

# 紧急度 → 默认通知策略（§4.3：紧急立即、一般攒着、低频只亮灯）可由 config.policy_by_urgency 覆盖
DEFAULT_POLICY_BY_URGENCY: dict[str, str] = {
    URGENCY_HIGH: POLICY_IMMEDIATE,
    URGENCY_MED: POLICY_ACCUMULATE,
    URGENCY_LOW: POLICY_LIGHT,
}

# 引擎路由结果状态（区别于 M3-M1 notify 的 DROPPED_*/DELIVERED 消费状态）
ROUTE_NONE = "route_none"                  # 无素材/无源，未生成候选
ROUTE_IRRELEVANT = "route_irrelevant"      # 相关性判定不通过
ROUTE_SUPPRESSED = "route_suppressed"      # 同类窗口聚合抑制（防风暴）
ROUTE_COOLDOWN = "route_cooldown"          # 冷却期内拦截
ROUTE_ACCUMULATED = "route_accumulated"    # 进入攒着缓冲（未立即投递）
ROUTE_LIGHT = "route_light"                # 只亮灯（未开口）
ROUTE_DELIVERED = "route_delivered"        # 立即投递（消费结果透传 M3-M1 状态）
ROUTE_FROZEN = "route_frozen"              # DORMANT 冻结：生成候选前早退（零投递）


def _default_bus():
    from backend.event_bus import bus
    return bus


def _to_date(value: Any):
    """归一化为 date；支持 datetime / date / 'YYYY-MM-DD' 字符串。无法解析返回 None。"""
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        try:
            return _dt.date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def grief_due(now: _dt.datetime, grief_schedule: Any) -> _dt.date | None:
    """哀伤节奏判定（§3 / §7）：grief_schedule 中有节点与 now 同日 → 到点（返回该节点）。

    grief_schedule 为 M1.6 喂入的 3/7/30 天节点列表（datetime/date/日期字符串均可）；
    纯函数、可注入 now，便于断言「到点触发 / 未到点不触发」。
    """
    nodes = grief_schedule or []
    for item in nodes:
        d = _to_date(item)
        if d is not None and d == now.date():
            return d
    return None


class EventTriggerEngine:
    """M3-M3 事件触发引擎：订阅 6 个已有事件 → 相关性/紧急度/策略/聚合/冷却 → 候选 → process()。

    依赖均可用关键字注入（便于测试替身；缺省懒加载真实单例）：
        bus        事件总线实例（默认全局 bus）
        notifier   M3-M1 ProactiveNotifier（或提供 .process() 的替身；必填）
        config     配置字典（relevance_block / urgency_map / policy_by_urgency /
                   emergency_passthrough / aggregate_window_seconds / cooldown_seconds /
                   grief_schedule / cold_snap_delta_c / cold_min_c / emergency_event_map）
        auth       授权中心实例（默认 AuthorizationCenter()；emergency_passthrough 的单一事实来源）
        now_fn     返回当前时刻（默认 datetime.now）
        content    可选内容源（含 build_candidate(event_type,payload)->str 或 callable；供候选草案）
        weather    天气源（today_temp_c()/tomorrow_min_c()；首版可注入 stub）
        schedule   日程源（tomorrow_agenda(now)->list；首版可注入 stub）
        aggregator 同类窗口聚合器（默认 EventWindowAggregator）
    """

    def __init__(
        self,
        bus: Any | None = None,
        notifier: Any | None = None,
        config: Mapping[str, Any] | None = None,
        auth: Any | None = None,
        now_fn: Callable[[], _dt.datetime] | None = None,
        content: Any | None = None,
        weather: Any | None = None,
        schedule: Any | None = None,
        aggregator: Any | None = None,
        dormant: Any | None = None,
    ) -> None:
        cfg = dict(config or {})
        self._config: dict[str, Any] = cfg
        self._bus = bus if bus is not None else _default_bus()
        self._notifier = notifier
        self._now_fn: Callable[[], _dt.datetime] = now_fn or _dt.datetime.now
        self._content = content
        self._weather = weather
        self._schedule = schedule
        # 同类窗口聚合（防事件风暴）；引擎时钟自持，窗判定由调用方显式传 now 时间戳
        self._aggregator = aggregator or EventWindowAggregator(
            window_seconds=float(cfg.get("aggregate_window_seconds", 1800)),
            now_fn=time.time,
        )
        # DORMANT 订阅协调器（M3-M4 dormant.DormantCoordinator / 或提供 is_frozen() 的替身）；
        # 缺省 None → 不冻结检查（向后兼容），供 M3-M4 在生成候选前查询 is_frozen() 早退。
        self._dormant = dormant
        # 通知策略 & 冷却（§4.3 / §5）
        self._policy_by_urgency: dict[str, str] = dict(
            cfg.get("policy_by_urgency", DEFAULT_POLICY_BY_URGENCY)
        )
        self._cooldown_seconds: float = float(cfg.get("cooldown_seconds", 900))
        self._relevance_block: list[str] = [str(x) for x in cfg.get("relevance_block", [])]
        self._urgency_map: dict[str, Any] = dict(cfg.get("urgency_map", {}))
        # 白名单单一事实来源：授权中心；热加载——_is_emergency 每次实时读。
        # 测试注入场景：cfg 显式携带时优先（静态覆盖，向后兼容）。
        self._passthrough_override: list[str] | None = (
            [str(x) for x in cfg["emergency_passthrough"]] if "emergency_passthrough" in cfg else None
        )
        self._auth = auth if auth is not None else AuthorizationCenter()
        self._grief_schedule: list = list(cfg.get("grief_schedule", []))
        self._emergency_event_map: dict[str, Any] = dict(cfg.get("emergency_event_map", {}))

        # 运行态：攒着缓冲 / 亮灯日志 / 冷却（按类型记最近路由时刻）
        self._pending: list[dict] = []
        self._light_log: list[dict] = []
        self._last_route_at: dict[str, float] = {}

        # 订阅 6 个已有事件（bus.on；事件名已在白名单，只订阅不新增）
        self._unsubs: list[Callable[[], None]] = []
        for evt in SUBSCRIBED_EVENTS:
            self._unsubs.append(self._bus.on(evt, partial(self._on_event, evt)))

    # =================================================================
    # ① 事件入口（bus.on handler）
    # =================================================================
    def _on_event(self, event_type: str, payload: Any = None) -> str:
        """事件到达 → 生成候选 → 路由（相关性/紧急度/策略/聚合/冷却）。"""
        candidate = self._build_candidate(event_type, payload or {})
        if candidate is None:
            return ROUTE_NONE
        return self._route_candidate(candidate)

    def close(self) -> None:
        """取消全部订阅（运行时清理；通常不调用，引擎为长生命周期单例）。"""
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass
        self._unsubs.clear()

    # =================================================================
    # ② 候选生成（按事件类型 → 候选草案 + 四维特征 + 聚合键）
    # =================================================================
    def _build_candidate(self, event_type: str, payload: Mapping[str, Any]) -> dict | None:
        if event_type == "schedule.anniversary":
            return self._candidate_anniversary(payload)
        if event_type == "env.anomaly":
            return self._candidate_env_anomaly(payload)
        if event_type == "device.state_changed":
            return self._candidate_device_state(payload)
        if event_type == "macro.state_changed":
            return self._candidate_macro_state(payload)
        if event_type == "memory.profile_updated":
            return self._candidate_memory_profile(payload)
        if event_type == "affect.updated":
            return self._candidate_affect(payload)
        return None

    def _candidate_anniversary(self, payload: Mapping[str, Any]) -> dict:
        evt = str(payload.get("事件", "") or payload.get("类型", ""))
        draft = self._content_draft("schedule.anniversary", payload) or f"今天是「{evt}」纪念日，我记得。"
        return {
            "类型": "纪念日",
            "内容草案": draft,
            "特征": {"urgency": 0.6, "actionability": 0.5, "relationship": 0.95, "freshness": 0.7},
            "_agg_key": "anniversary",
            "_event_type": "schedule.anniversary",
        }

    def _candidate_env_anomaly(self, payload: Mapping[str, Any]) -> dict:
        sensor = str(payload.get("传感器", ""))
        val = payload.get("数值")
        draft = self._content_draft("env.anomaly", payload) or f"检测到{sensor}异常（{val}）。"
        cand: dict = {
            "类型": "环境异常",
            "内容草案": draft,
            "特征": {"urgency": 0.7, "actionability": 0.6, "relationship": 0.2, "freshness": 0.8},
            "_agg_key": f"env.{sensor}",
            "_event_type": "env.anomaly",
        }
        et = self._emergency_type_for("env.anomaly", sensor)
        if et:
            cand["紧急类型"] = et
        return cand

    def _candidate_device_state(self, payload: Mapping[str, Any]) -> dict:
        entity = str(payload.get("entity", ""))
        draft = self._content_draft("device.state_changed", payload) or f"设备「{entity}」状态已变更。"
        return {
            "类型": "设备变更",
            "内容草案": draft,
            "特征": {"urgency": 0.5, "actionability": 0.4, "relationship": 0.3, "freshness": 0.5},
            "_agg_key": f"device.{entity}",
            "_event_type": "device.state_changed",
        }

    def _candidate_macro_state(self, payload: Mapping[str, Any]) -> dict:
        new_state = str(payload.get("后态", payload.get("state", "")))
        draft = self._content_draft("macro.state_changed", payload) or f"当前宏观状态：{new_state}。"
        return {
            "类型": "宏观状态",
            "内容草案": draft,
            "特征": {"urgency": 0.3, "actionability": 0.2, "relationship": 0.3, "freshness": 0.4},
            "_agg_key": "macro.state",
            "_event_type": "macro.state_changed",
        }

    def _candidate_memory_profile(self, payload: Mapping[str, Any]) -> dict:
        ver = str(payload.get("版本", ""))
        draft = self._content_draft("memory.profile_updated", payload) or f"你的画像已更新（{ver}）。"
        return {
            "类型": "画像更新",
            "内容草案": draft,
            "特征": {"urgency": 0.3, "actionability": 0.3, "relationship": 0.4, "freshness": 0.5},
            "_agg_key": "memory.profile",
            "_event_type": "memory.profile_updated",
        }

    def _candidate_affect(self, payload: Mapping[str, Any]) -> dict:
        mood = str(payload.get("mood", ""))
        draft = self._content_draft("affect.updated", payload) or f"你现在的情绪状态：{mood}。"
        return {
            "类型": "情感更新",
            "内容草案": draft,
            "特征": {"urgency": 0.4, "actionability": 0.2, "relationship": 0.6, "freshness": 0.5},
            "_agg_key": "affect",
            "_event_type": "affect.updated",
        }

    def _content_draft(self, event_type: str, payload: Mapping[str, Any]) -> str | None:
        """可选内容源提供候选草案文案；异常/无素材回退内置模板。"""
        c = self._content
        if c is None:
            return None
        try:
            if hasattr(c, "build_candidate"):
                res = c.build_candidate(event_type, payload)
            else:
                res = c(event_type, payload)
            if isinstance(res, str) and res.strip():
                return res
        except Exception:  # noqa: BLE001
            logger.debug("content 源异常，回退内置模板", exc_info=True)
        return None

    def _emergency_type_for(self, event_type: str, sub: str) -> str | None:
        """按用户配置的 emergency_event_map 映射出候选的「紧急类型」：

        {event_type: {subkey: "紧急类型"}}，如 {"env.anomaly": {"smoke": "smoke_detected"}}。
        仅当映射命中才赋予紧急类型；最终穿透与否仍以 emergency_passthrough 清单为准。
        """
        node = self._emergency_event_map.get(event_type, {})
        if isinstance(node, dict):
            return node.get(sub)
        return None

    # =================================================================
    # ③ 候选路由：相关性 → 紧急度 → 紧急穿透 → 聚合 → 冷却 → 策略
    # =================================================================
    def _route_candidate(self, candidate: Mapping[str, Any]) -> str:
        """执行一条候选的完整触发管线，返回路由状态（见模块顶部常量）。"""
        # ⓪ DORMANT 冻结（§4.4）：生成候选前早退，零投递（M3-M1 notify 仍是消费端最终闸门，双保险）
        if self._is_frozen():
            logger.info("[m3.event_trigger] DORMANT 冻结，候选不生成不投递: %s", candidate.get("类型"))
            return ROUTE_FROZEN

        # ① 相关性判定（§4.3：是否与用户相关）
        if not self._is_related(candidate):
            logger.info("[m3.event_trigger] 相关性判定不通过，丢弃: %s", candidate.get("类型"))
            return ROUTE_IRRELEVANT

        # ② 紧急度分级（§4.3：low/med/high）
        urgency = self._urgency_for(candidate)

        # ③ 紧急穿透（§5：仅限用户配置的 emergency_passthrough 清单）
        emergency = self._is_emergency(candidate)

        # ④ 同类窗口聚合（防事件风暴）；紧急穿透例外（不抑制）
        agg_key = str(candidate.get("_agg_key", candidate.get("类型", "")))
        if not emergency and not self._aggregator.should_trigger(agg_key, self._now_ts()):
            logger.info("[m3.event_trigger] 同类窗口聚合抑制（防风暴）: %s", agg_key)
            return ROUTE_SUPPRESSED

        # ⑤ 冷却检查（§4.3/§5）；紧急穿透例外
        ctype = str(candidate.get("类型", ""))
        if not emergency and self._in_cooldown(ctype):
            logger.info("[m3.event_trigger] 冷却期拦截: %s", ctype)
            return ROUTE_COOLDOWN

        # ⑥ 通知策略（立即/攒着/只亮灯）
        policy = self._policy_for(candidate, urgency, emergency)
        if policy == POLICY_IMMEDIATE:
            status = self._notifier.process(candidate)
            self._note_route(ctype)
            return status if status is not None else ROUTE_DELIVERED
        if policy == POLICY_ACCUMULATE:
            self._pending.append(dict(candidate))
            return ROUTE_ACCUMULATED
        # POLICY_LIGHT：只亮灯，不开口
        self._light_log.append({
            "ts": self._now_ts(),
            "类型": ctype,
            "内容草案": candidate.get("内容草案"),
        })
        return ROUTE_LIGHT

    # ---- DORMANT 冻结（§4.4 生成候选前早退） ----
    def _is_frozen(self) -> bool:
        """DORMANT 冻结查询（M3-M4 dormant.DormantCoordinator）；缺省无协调器 → 不冻结。

        DORMANT 冻结是 M0 硬约束（is_proactive_allowed()），本引擎在生成候选前查一次早退，
        避免冻结期仍生成候选再被 M3-M1 notify.process() 丢弃。
        """
        if self._dormant is None:
            return False
        try:
            return bool(self._dormant.is_frozen())
        except Exception:  # noqa: BLE001
            return False

    # ---- 相关性判定（§4.3：是否与用户相关；默认全部相关，config.relevance_block 排除） ----
    def _is_related(self, candidate: Mapping[str, Any]) -> bool:
        ctype = str(candidate.get("类型", ""))
        return ctype not in self._relevance_block

    # ---- 紧急度分级（§4.3：low/med/high） ----
    def _urgency_for(self, candidate: Mapping[str, Any]) -> str:
        if self._is_emergency(candidate):
            return URGENCY_HIGH
        ctype = str(candidate.get("类型", ""))
        if ctype in self._urgency_map:
            return str(self._urgency_map[ctype])
        feats = candidate.get("特征", {})
        total = 0.0
        try:
            total = total_score(feats)
        except Exception:  # noqa: BLE001
            total = float(sum(float(feats.get(k, 0.0)) * float(WEIGHTS.get(k, 0.0)) for k in WEIGHTS))
        if total >= 0.7:
            return URGENCY_HIGH
        if total >= 0.4:
            return URGENCY_MED
        return URGENCY_LOW

    # ---- 通知策略（立即/攒着/只亮灯；紧急穿透与关系价值爆表 → 立即） ----
    def _policy_for(self, candidate: Mapping[str, Any], urgency: str, emergency: bool) -> str:
        if emergency or self._is_relationship_boom(candidate):
            return POLICY_IMMEDIATE
        return self._policy_by_urgency.get(urgency, POLICY_ACCUMULATE)

    # ---- 关系价值单项爆表（§4.1 生日/纪念日 → 豁免穿透、不占额度） ----
    def _is_relationship_boom(self, candidate: Mapping[str, Any]) -> bool:
        feats = candidate.get("特征", {})
        return float(feats.get("relationship", 0.0)) >= float(RELATIONSHIP_BOOM_THRESHOLD)

    # ---- 紧急穿透（§5：仅限用户配置清单；与 M3-M1 notify._is_emergency 一致） ----
    def _is_emergency(self, candidate: Mapping[str, Any]) -> bool:
        et = str(candidate.get("紧急类型", ""))
        passthrough = (
            self._passthrough_override
            if self._passthrough_override is not None
            else [str(x) for x in self._auth.get().get("emergency_passthrough", [])]
        )
        return bool(et) and et in passthrough

    # ---- 冷却检查（§4.3/§5） ----
    def _in_cooldown(self, ctype: str) -> bool:
        if self._cooldown_seconds <= 0:
            return False
        last = self._last_route_at.get(ctype)
        if last is None:
            return False
        return (self._now_ts() - last) < self._cooldown_seconds

    def _note_route(self, ctype: str) -> None:
        self._last_route_at[ctype] = self._now_ts()

    def _now_ts(self) -> float:
        return self._now_fn().timestamp()

    # =================================================================
    # ④ 哀伤节点（§3 / §7：3/7/30 天，M1.6 喂入）
    # =================================================================
    def check_grief(self, now: _dt.datetime | None = None) -> str | None:
        """哀伤节奏：grief_schedule 节点到点 → 生成哀伤候选并路由（走关系价值/穿透豁免判定）。"""
        now = now or self._now_fn()
        node = grief_due(now, self._grief_schedule)
        if node is None:
            return None
        candidate = self._build_grief_candidate(now, node)
        return self._route_candidate(candidate)

    def _build_grief_candidate(self, now: _dt.datetime, node: _dt.date) -> dict:
        return {
            "类型": "哀伤节点",
            "内容草案": "这些日子，我陪着你。",
            "特征": {"urgency": 0.5, "actionability": 0.3, "relationship": 0.9, "freshness": 0.6},
            "_agg_key": "grief",
            "_event_type": "grief",
        }

    # =================================================================
    # ⑤ 预判型触发（§4.3 / §8 开放问题 3：只上线天气+日程两源）
    # =================================================================
    def check_preemptive(self, now: _dt.datetime | None = None) -> str | None:
        """天气（降/突变）+ 明日日程两源 join → 预判候选（如「明早降温记得加衣」）。

        历史偏好 join 首版可 stub/省略；未接真实数据源前用可注入 weather/schedule stub。
        """
        now = now or self._now_fn()
        weather = self._weather
        if weather is None:
            return None
        if not self._is_cold_snap(weather):
            return None
        agenda = self._tomorrow_agenda(now)
        if not agenda:
            return None
        candidate = self._build_preemptive_candidate(now, agenda, weather)
        return self._route_candidate(candidate)

    def _is_cold_snap(self, weather: Any) -> bool:
        today = _safe_float(weather.today_temp_c()) if hasattr(weather, "today_temp_c") else None
        tmrw = _safe_float(weather.tomorrow_min_c()) if hasattr(weather, "tomorrow_min_c") else None
        if today is None or tmrw is None:
            return False
        delta = float(self._config.get("cold_snap_delta_c", 5.0))
        min_c = float(self._config.get("cold_min_c", 8.0))
        return (today - tmrw) >= delta and tmrw <= min_c

    def _tomorrow_agenda(self, now: _dt.datetime) -> list:
        if self._schedule is None:
            return []
        try:
            if hasattr(self._schedule, "tomorrow_agenda"):
                return list(self._schedule.tomorrow_agenda(now) or [])
            return list(self._schedule(now) or [])
        except Exception:  # noqa: BLE001
            logger.debug("日程源异常，视为无明日日程", exc_info=True)
            return []

    def _build_preemptive_candidate(self, now: _dt.datetime, agenda: list, weather: Any) -> dict:
        today = _safe_float(weather.today_temp_c())
        tmrw = _safe_float(weather.tomorrow_min_c())
        title = ""
        if agenda and isinstance(agenda[0], dict):
            title = str(agenda[0].get("title", "") or "")
        if title:
            draft = f"明早降温，出门记得加衣；你还有「{title}」的安排。"
        else:
            draft = f"明早降温（今{today:.0f}°→明早{tmrw:.0f}°），出门记得加衣。"
        return {
            "类型": "预判·加衣",
            "内容草案": draft,
            "特征": {"urgency": 0.9, "actionability": 0.9, "relationship": 0.3, "freshness": 0.5},
            "_agg_key": "preemptive.clothes",
            "_event_type": "preemptive",
        }

    # =================================================================
    # ⑥ 消费者（M3-M1 notify.process）：攒着缓冲 flush（批量投递）
    # =================================================================
    def flush_pending(self) -> list[str]:
        """把「攒着」的候选批量交给 notifier.process() 投递；返回逐条消费状态。"""
        statuses: list[str] = []
        while self._pending:
            cand = self._pending.pop(0)
            status = self._notifier.process(cand)
            statuses.append(status if status is not None else ROUTE_DELIVERED)
            self._note_route(str(cand.get("类型", "")))
        return statuses

    # ---- 供测试/诊断读取的运行态 ----
    @property
    def pending(self) -> list[dict]:
        return list(self._pending)

    @property
    def light_log(self) -> list[dict]:
        return list(self._light_log)

    @property
    def aggregator(self) -> Any:
        return self._aggregator


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "SUBSCRIBED_EVENTS",
    "URGENCY_LOW",
    "URGENCY_MED",
    "URGENCY_HIGH",
    "POLICY_IMMEDIATE",
    "POLICY_ACCUMULATE",
    "POLICY_LIGHT",
    "DEFAULT_POLICY_BY_URGENCY",
    "ROUTE_NONE",
    "ROUTE_IRRELEVANT",
    "ROUTE_SUPPRESSED",
    "ROUTE_COOLDOWN",
    "ROUTE_ACCUMULATED",
    "ROUTE_LIGHT",
    "ROUTE_DELIVERED",
    "grief_due",
    "EventTriggerEngine",
]
