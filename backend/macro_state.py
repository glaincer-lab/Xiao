"""宏观在场状态机（M0·T5，MVP 唯一阻塞项）。

实现 M0-core §3/§4.1 的宏观四态：ACTIVE / IDLE / DORMANT / RETURNING。

模式（写死，对应 M0-core §4.1）：
    ACTIVE（有交互）─空闲>15min─►IDLE ─无交互7天─►DORMANT ─用户主动对话─►RETURNING ─新交互─►ACTIVE

三条 DORMANT 纪律（安全行为唯一载体，见 tests/test_macro_state.py）：
  ① DORMANT 期间主动事件总线零消息（is_proactive_allowed() 冻结闸门）
  ② 零归因（本模块绝不输出「用户是不是讨厌我」类文本）
  ③ 托付后台任务照办但不推送（on_background_completion 只进回归简报）

事件契约：发生状态转换时发布 `macro.state_changed` {前态,后态,时长}（M3/M2/M6 订阅，DORMANT 触发各方冻结）。
事件名已登记 `backend/event_bus.py` 的 EVENT_TYPES 白名单，本模块只订阅/发布、不重复新增登记。

决策锚点：
- 决策 4.3：RETURNING 只由「用户主动发起对话」触发；通知点击/后台完成不算（on_non_dialogue_interaction）。
- 决策 4.5：DORMANT 期间情感衰减暂停（is_affect_decay_paused() 留 M2 联动钩子，不实装 M2）。
- 边界：不实现 M3 主动引擎（总闸门滑块可先空挂）；DORMANT 冻结是安全行为唯一载体。

仅供标准库；MIT。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.event_bus import bus

# ---- 四个宏观状态（单一事实来源） ----
MACRO_STATES: tuple[str, str, str, str] = ("ACTIVE", "IDLE", "DORMANT", "RETURNING")
ACTIVE, IDLE, DORMANT, RETURNING = MACRO_STATES

# ---- 状态转换阈值（M0-core §4.1 写死） ----
IDLE_AFTER_SECONDS: int = 15 * 60            # ACTIVE 空闲 >15min → IDLE
DORMANT_AFTER_SECONDS: int = 7 * 24 * 3600   # IDLE 无交互 7 天 → DORMANT

# ---- RETURNING 分层问候（M0-core §4.1） ----
SHORT_WITHIN_DAYS = 3          # ≤3 天：层「short」
LONG_AFTER_DAYS = 60           # ≥2 个月：层「long」（中间 3 天~2 个月安全默认「mid」）

TIER_GREETING: dict[str, str] = {
    "short": "几天没见",
    "mid":   "有一阵子了",
    "long":  "好久好久不见",
}
TIER_TAIL: dict[str, str] = {
    "short": "",                       # 无额外尾巴
    "mid":   "慢慢来",
    "long":  "最近有什么新变化可以告诉我的？",  # 主动权交还
}
TIER_BRIEF_MAX: dict[str, int] = {
    "short": 2,
    "mid":   3,
    "long":  1,
}


def _default_state_path() -> Path:
    """持久化路径：基于 __file__ 相对项目根定位，不写死本机绝对路径。"""
    return Path(__file__).resolve().parent.parent / "runtime" / "macro_state.json"


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


# ============================================================
# 纯函数：RETURNING 分层问候（便于测试，也可被实例方法委托）
# ============================================================
def greeting_tier(duration_seconds: float) -> str:
    """按距上次用户交互时长分档：≤3天→short；≥2个月→long；其余→mid。

    中间 3 天~2 个月按规范字面只给了「≤2 周」，此处以更温和的「mid（有一阵子了）」
    作为安全默认（避免过早说「好久好久不见」/追问去向）。
    """
    days = float(duration_seconds) / 86400.0
    if days >= LONG_AFTER_DAYS:
        return "long"
    if days <= SHORT_WITHIN_DAYS:
        return "short"
    return "mid"


def build_returning_brief(
    source_a: list[str] | None = None,
    source_b: list[str] | None = None,
    source_c: list[str] | None = None,
) -> list[str]:
    """回归简报三源合并 + 多样性熔断阀（M0-core §4.1 / 审计 R3）。

    源 A 系统足迹（Git 提交/HA 记录/系统日志）｜源 B 小二自身足迹（仅点缀）｜
    源 C persona/lorebook/ 抽取。按 A→B→C 顺序合并，交由 returning_greeting 按档截断。

    【熔断阀】：若源 A 为空（用户长期不在场、无系统足迹），则源 B 必须被钳制为**最多 1 条**
    且改用浪漫化表达，避免简报退化为「长任务运维日志大篇幅轰炸」的冰冷机器感（R3）。
    """
    source_a = source_a or []
    source_b = source_b or []
    source_c = source_c or []

    if not source_a:
        # 熔断：源 A 为空 → 用户完全离线，源 B 只输出 1 条浪漫化概述，杜绝运维日志刷屏。
        out: list[str] = []
        if source_b:
            total_tasks = len(source_b)
            out.append(
                f"在你不在的这段日子里，我自己在后台默默打理了 {total_tasks} 个数字小任务，"
                "把本地数据库擦拭得干干净净。"
            )
        if source_c:
            out.append(str(source_c[0]))
        return out

    # 常规（源 A 非空）：A→B→C 顺序合并。
    out = [str(x) for x in source_a if x]
    if source_b:
        out.append(str(source_b[0]))
    if source_c:
        out.append(str(source_c[0]))
    return out


def returning_greeting(
    duration_seconds: float,
    brief_items: list[str] | None = None,
) -> str:
    """生成 RETURNING 分层问候文本。

    - 按 duration 选档（≤3天/≤2周×）/（≥2个月）。
    - 简报按档预算截断（short≤2 / mid≤3 / long≤1），缺省则无简报。
    - 永不追问去向；long 档尾巴把主动权交还用户。
    """
    tier = greeting_tier(duration_seconds)
    parts: list[str] = [TIER_GREETING[tier]]
    selected = (brief_items or [])[: TIER_BRIEF_MAX[tier]]
    if selected:
        parts.append("\n".join(f"- {item}" for item in selected))
    tail = TIER_TAIL[tier]
    if tail:
        parts.append(tail)
    return "\n".join(parts)


# ============================================================
# 状态机
# ============================================================
class MacroStateMachine:
    """线程保持宏观在场状态；事件走 event_bus，不跨模块直调。

    用法:
        sm = MacroStateMachine()
        sm.tick()                 # 心跳：ACTIVE→IDLE→DORMANT（时间驱动）
        sm.on_user_dialogue()     # 用户主动对话：DORMANT→RETURNING→ACTIVE
        sm.save()                 # 持久化最后状态
    """

    def __init__(
        self,
        state: str = ACTIVE,
        last_interaction: datetime | None = None,
        dormant_since: datetime | None = None,
        persist_path: str | Path | None = None,
        event_bus: Any | None = None,
    ) -> None:
        if state not in MACRO_STATES:
            raise ValueError(f"未知宏观状态：{state!r}。应为 {'/'.join(MACRO_STATES)}")
        self.state: str = state
        self.last_interaction: datetime | None = last_interaction
        self.dormant_since: datetime | None = dormant_since
        self._persist_path: Path = Path(persist_path) if persist_path else _default_state_path()
        self._bus: Any = event_bus if event_bus is not None else bus
        self._regression_items: list[str] = []
        self._returning_duration: float = 0.0

    # ---- 查询 ----
    def current_state(self) -> str:
        return self.state

    def is_dormant(self) -> bool:
        return self.state == DORMANT

    def is_proactive_allowed(self) -> bool:
        """主动引擎冻结闸门（M3 仲裁器据此挂起）。DORMANT→False，其余→True。"""
        return self.state != DORMANT

    def is_affect_decay_paused(self) -> bool:
        """DORMANT 期间情感衰减暂停（决策 4.5 · M2 联动钩子，不实装 M2）。"""
        return self.state == DORMANT

    # ---- 时间驱动：ACTIVE→IDLE→DORMANT ----
    def tick(self, now: datetime | None = None) -> dict[str, Any] | None:
        """心跳推进：空闲>15min IDLE；无交互7天 DORMANT。

        仅当发生状态转换时发布 macro.state_changed 并返回事件 payload；否则返回 None。
        """
        now = now or datetime.now()
        old = self.state
        dur = self._duration_seconds(now, self.last_interaction)
        if old == ACTIVE and dur > IDLE_AFTER_SECONDS:
            self.state = IDLE
        elif old == IDLE and dur > DORMANT_AFTER_SECONDS:
            self.state = DORMANT
            self.dormant_since = now
        else:
            return None
        return self._emit(old, self.state, dur)

    # ---- 用户主动对话驱动（决策 4.3） ----
    def on_user_dialogue(self, now: datetime | None = None) -> dict[str, Any] | None:
        """用户主动发起对话。

        - DORMANT → RETURNING（首次唤醒，捕获休眠时长供分层问候）
        - RETURNING → ACTIVE（用户继续对话）
        - IDLE → ACTIVE（用户回来）
        - ACTIVE → 保持（刷新 last_interaction，不发事件）
        返回 macro.state_changed 事件 payload（转换时）或 None。
        """
        now = now or datetime.now()
        old = self.state
        dur = self._duration_seconds(now, self.last_interaction)
        if old == DORMANT:
            self.state = RETURNING
            self._returning_duration = dur
            self.dormant_since = None
        elif old == RETURNING:
            self.state = ACTIVE
        elif old == IDLE:
            self.state = ACTIVE
        else:  # ACTIVE 保持：刷新最近交互，不发事件
            self.last_interaction = now
            return None
        self.last_interaction = now
        return self._emit(old, self.state, dur)

    # ---- 非用户主动对话（决策 4.3）：不触发 RETURNING ----
    def on_non_dialogue_interaction(self, now: datetime | None = None) -> None:
        """通知点击/后台完成等非主动交互：不改变宏观状态、不触发 RETURNING。

        仅作占位；宏观状态机不消费这类信号（避免把「用户的被动动作」误判为「回响」）。
        """
        return None

    # ---- 托付后台任务：照办但不推送（三纪律③） ----
    def on_background_completion(self, item: str, now: datetime | None = None) -> bool:
        """托付后台任务完成：照办（记录进展进回归简报），不推送、不改宏观状态。

        返回 True 表示已照办记录（进展只进回归简报，DORMANT 下亦如此）。
        """
        if item:
            self._regression_items.append(str(item))
        return True

    def regression_brief(self) -> str:
        """回归简报（零归因）：仅汇总托付后台任务进展，绝不输出本模块的自我怀疑文本。"""
        if not self._regression_items:
            return ""
        return "回归简报：" + "；".join(self._regression_items)

    # ---- RETURNING 分层问候（实例便捷方法，委托纯函数） ----
    def returning_greeting(self, brief_items: list[str] | None = None) -> str:
        dur = self._returning_duration if self.state == RETURNING else self._duration_seconds(
            datetime.now(), self.last_interaction
        )
        return returning_greeting(dur, brief_items)

    # ---- 持久化最后状态 ----
    def save(self) -> None:
        data = {
            "state": self.state,
            "last_interaction": self.last_interaction.isoformat() if self.last_interaction else None,
            "dormant_since": self.dormant_since.isoformat() if self.dormant_since else None,
        }
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        self._persist_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, persist_path: str | Path | None = None, event_bus: Any | None = None) -> "MacroStateMachine":
        path = Path(persist_path) if persist_path else _default_state_path()
        obj = cls(state=ACTIVE, last_interaction=None, persist_path=path, event_bus=event_bus)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                obj.state = data.get("state", ACTIVE)
                obj.last_interaction = _parse_dt(data.get("last_interaction"))
                obj.dormant_since = _parse_dt(data.get("dormant_since"))
            except (json.JSONDecodeError, KeyError):
                obj.state = ACTIVE
        return obj

    # ---- 内部 ----
    @staticmethod
    def _duration_seconds(now: datetime, last: datetime | None) -> float:
        if last is None:
            return 0.0
        return max(0.0, (now - last).total_seconds())

    def _emit(self, old: str, new: str, dur: float) -> dict[str, Any]:
        payload: dict[str, Any] = {"前态": old, "后态": new, "时长": round(dur, 3)}
        self._bus.emit("macro.state_changed", payload)
        return payload


# 模块级内存单例（M0-core §3「macro_state」）；M3/M2/M6 等模块在其上订阅/读取。
macro_state = MacroStateMachine()


__all__ = [
    "MACRO_STATES",
    "ACTIVE",
    "IDLE",
    "DORMANT",
    "RETURNING",
    "IDLE_AFTER_SECONDS",
    "DORMANT_AFTER_SECONDS",
    "TIER_GREETING",
    "TIER_TAIL",
    "TIER_BRIEF_MAX",
    "greeting_tier",
    "build_returning_brief",
    "returning_greeting",
    "MacroStateMachine",
    "macro_state",
]
