"""M3-M1 预算制候选消费：消费流程 + 勿扰检查 + 事件发布（backend/m3/notify.py）。

实现 M3-proactive.md §4.1 候选消费流程：
    候选生成 → 四维打分排名
      → [关系价值单项爆表（生日/纪念日）→ 豁免穿透，不占额度]
      → 总分 >0.6 → 勿扰检查（时段/冷却/注意力）→ 消费 1 条额度 → 开口
      → 总分 ≤0.6 → 静默丢弃（记日志供调参）

硬护栏（§5）：勿扰时段 + 全局冷却 + 每日额度上限；紧急穿透例外（用户可配清单）。
全屏冻结（§4.1）：attention.fullscreen on → 候选丢弃不攒，零投递。
DORMANT 联动（§4.4）：macro_state.is_proactive_allowed() False → 冻结、零消息零消费。

事件契约（§6，事件名已在 event_bus.EVENT_TYPES 白名单，本包只发布、不新增登记）：
    proactive.candidate  {类型,四维分,内容草案}  预算消费前
    proactive.delivered  {id,用户响应}          开口后（三态反馈入记忆由订阅端处理）

边界：本包只发布事件；订阅端（画像回写/仲裁）留给后续包；主动类只建议不执行。

仅供标准库；MIT。
"""
from __future__ import annotations

import datetime as _dt
import logging
import time
import uuid
from typing import Any, Callable, Mapping

from backend.m3.budget import ProactiveBudget
from backend.m3.score import DIMS, TOTAL_THRESHOLD, is_relationship_boom, score_candidate

# ---- 消费结果（可比较状态字符串） ----
DROPPED_DORMANT = "dropped_dormant"                    # DORMANT 冻结
DROPPED_FULLSCREEN = "dropped_fullscreen"              # 全屏冻结
DROPPED_SCORE_LOW = "dropped_score_low"                # 总分 ≤0.6 静默丢弃
DROPPED_QUOTA_EXCEEDED = "dropped_quota_exceeded"      # 额度用满
DROPPED_DND = "dropped_dnd"                            # 勿扰拦截
DELIVERED = "delivered"                                # 开口投递
SHADOW_RECORDED = "shadow_recorded"              # 影子期只记录不真投（M3-M6）

logger = logging.getLogger("m3.notify")


def _default_bus():
    from backend.event_bus import bus
    return bus


class ProactiveNotifier:
    """M3 主动候选消费器：打分 → 豁免 → 勿扰 → 消费 → 开口，全程走 event_bus 事件。

    依赖均可用关键字注入（便于测试替身；缺省懒加载真实单例）：
        budget  ProactiveBudget（默认新建）
        sensor  提供 is_fullscreen()/is_idle()（默认 backend.attention.AttentionSensor）
        macro   提供 is_proactive_allowed()（默认 backend.macro_state.macro_state）
        bus     事件总线实例（默认全局 bus）
        config  勿扰/冷却/注意力/紧急穿透 配置字典
    """

    def __init__(
        self,
        budget: ProactiveBudget | None = None,
        sensor: Any | None = None,
        macro: Any | None = None,
        bus: Any | None = None,
        config: Mapping[str, Any] | None = None,
        now_fn: Callable[[], float] | None = None,
        shadow: bool = False,
        shadow_recorder: Any | None = None,
    ) -> None:
        self._budget = budget if budget is not None else ProactiveBudget()
        self._sensor = sensor
        self._macro = macro
        self._bus = bus if bus is not None else _default_bus()
        cfg = dict(config or {})
        self._dnd_hours = list(cfg.get("dnd_hours", []))              # [(start_hour, end_hour), ...]
        self._cooldown_seconds = float(cfg.get("cooldown_seconds", 900))  # 全局冷却（秒）
        # block_when_idle | allow_when_idle | off
        self._attention_policy = str(cfg.get("attention_policy", "block_when_idle"))
        self._emergency_passthrough = list(cfg.get("emergency_passthrough", []))  # 用户可配紧急清单
        self._now_fn = now_fn or time.time
        self._last_delivered_at: float | None = None
        self._shadow = bool(shadow)
        self._shadow_recorder = shadow_recorder

    # ---- 主入口：执行一条候选的消费流程 ----
    def process(self, candidate: Mapping[str, Any]) -> str:
        """按 §4.1 消费一条候选，返回消费结果状态（见模块顶部常量）。"""
        ctype = str(candidate.get("类型", ""))
        draft = str(candidate.get("内容草案", ""))
        features = candidate.get("特征", candidate.get("四维分", {})) or {}

        # ① DORMANT 冻结闸门（§4.4）：冻结非降频，零消息零消费零归因
        if not self._get_macro().is_proactive_allowed():
            logger.info("[m3.notify] DORMANT 冻结，候选丢弃: %s", ctype)
            return DROPPED_DORMANT

        # ② 全屏冻结（§4.1）：候选丢弃不攒（防集中轰炸），零投递
        if self._get_sensor().is_fullscreen():
            logger.info("[m3.notify] 全屏期，候选丢弃不攒: %s", ctype)
            return DROPPED_FULLSCREEN

        # ③ 四维打分
        score = score_candidate(features)
        total = score["total"]

        # ④ 总分 ≤0.6 → 静默丢弃（记日志供调参），不发任何事件
        if total <= TOTAL_THRESHOLD:
            logger.info(
                "[m3.notify] 候选总分 %.3f ≤ %s，静默丢弃: %s（特征=%s）",
                total, TOTAL_THRESHOLD, ctype, features,
            )
            return DROPPED_SCORE_LOW

        exempt_quota = is_relationship_boom(score)
        is_emergency = self._is_emergency(candidate)

        # ⑤ 发布 proactive.candidate（预算消费前）；四维分含加权总分（供仲裁器使用）
        four_dim = {k: score[k] for k in DIMS}
        four_dim["total"] = score["total"]
        self._bus.emit("proactive.candidate", {
            "类型": ctype,
            "四维分": four_dim,
            "内容草案": draft,
        })

        # ⑥ 勿扰检查（前置：勿扰命中不消费额度、不投递）；紧急穿透例外（§5）
        if not is_emergency and self._dnd_blocks():
            logger.info("[m3.notify] 勿扰拦截，候选丢弃（未消费额度）: %s", ctype)
            return DROPPED_DND

        # ⑦ 消费 1 条额度（关系爆表豁免 / 紧急穿透 不占额度）
        if not (exempt_quota or is_emergency):
            if not self._budget.can_consume(1):
                logger.info("[m3.notify] 当日额度已满，候选丢弃: %s", ctype)
                return DROPPED_QUOTA_EXCEEDED
            self._budget.consume(1)

        # ⑧ 开口（影子期：只记录不真投）
        delivery_id = uuid.uuid4().hex[:12]
        self._last_delivered_at = self._now_fn()
        if self._shadow:
            if self._shadow_recorder is not None:
                self._shadow_recorder.record({
                    "id": delivery_id,
                    "类型": ctype,
                    "四维分": four_dim,
                    "内容草案": draft,
                    "是否达阈": True,
                    "将消费": 0 if (exempt_quota or is_emergency) else 1,
                    "时间戳": self._now_fn(),
                })
            logger.info("[m3.notify] 影子期记录（不真投）: %s id=%s", ctype, delivery_id)
            return SHADOW_RECORDED
        self._bus.emit("proactive.delivered", {
            "id": delivery_id,
            "用户响应": str(candidate.get("用户响应", "")),
        })
        logger.info(
            "[m3.notify] 已开口投递: %s id=%s 四维分=%s", ctype, delivery_id, score
        )
        return DELIVERED

    # ---- 勿扰检查 ----
    def _dnd_blocks(self) -> bool:
        """勿扰时段 + 全局冷却 + 注意力（is_idle）→ 任一命中则拦截。由配置可注入。"""
        now = self._now_fn()
        if self._in_dnd_hours(now):
            return True
        if self._last_delivered_at is not None and (
            now - self._last_delivered_at
        ) < self._cooldown_seconds:
            return True
        if self._attention_policy == "block_when_idle" and self._get_sensor().is_idle():
            return True
        return False

    def _in_dnd_hours(self, epoch: float) -> bool:
        """当前小时是否落在勿扰时段（支持跨午夜，如 22~7）。"""
        hour = _dt.datetime.fromtimestamp(epoch).hour
        for pair in self._dnd_hours:
            sh, eh = int(pair[0]), int(pair[1])
            if sh <= eh:
                if sh <= hour < eh:
                    return True
            else:  # 跨午夜
                if hour >= sh or hour < eh:
                    return True
        return False

    # ---- 紧急穿透（§5：什么算紧急，用户可配清单） ----
    def _is_emergency(self, candidate: Mapping[str, Any]) -> bool:
        et = str(candidate.get("紧急类型", ""))
        return bool(et) and et in self._emergency_passthrough

    # ---- 依赖懒加载（测试注入替身为 None 时走真实单例；无硬编码本机路径） ----
    def _get_sensor(self):
        if self._sensor is None:
            from backend.attention import AttentionSensor
            self._sensor = AttentionSensor()
        return self._sensor

    def _get_macro(self):
        if self._macro is None:
            from backend.macro_state import macro_state
            self._macro = macro_state
        return self._macro
