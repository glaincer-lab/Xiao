"""M3-M2 心跳引擎（backend/m3/heartbeat.py）。

实现 M3-proactive.md §4.2 心跳：
    定时（早/晚/时段）→ 查记忆+日程 → 生成候选 → 内容质量门（无素材宁可不说）
      → 消费预算（调用 M3-M1 notify.process()）→ 开口。
    无响应退避：标记已说不追问；连续 N 天无响应自动降频；回应率进 style_profile 反向调节。

设计要点（可测试性优先）：
    - 核心逻辑（_should_run/_run_once/_should_decay/note_user_response）为同步、
      可注入 now_fn 断言，不真实 sleep。
    - start()/stop() 用 asyncio 后台协程周期性（如每分钟）检查 _should_run(now)，
      命中则 _run_once(now)。定时机制借鉴 backend/tasks.py 的 asyncio.create_task
      后台模式（但本包是"周期心跳"，非 tasks.py 的"用户交办即时任务"）。
    - 时段三档（§8 开放问题 2：首版固定三档 early/mid/evening，可配置 hour 区间）。

无响应退避：
    - 投递后记录（_record_delivery），用户无回应则逐日累加 no_response_streak；
    - 连续 decay_after_days（默认 3）天无响应 → _should_decay() 为真 → 自动降频；
    - note_user_response() 由会话层在用户发言时重置 streak（内部方法，不新增总线事件）；
    - 回应率进 style_profile 由 M3-M5 反向调节；本包只记录投递/回应，不建模心理标签，
      反向调节逻辑留接口（_apply_decay 为降频动作，可被外部 style 逻辑复用）。

边界（写死）：
    - 本包不新增事件/不改 EVENT_REGISTRY/event_bus 白名单（proactive.* 已够）；
    - 主动类只建议不执行；消费逻辑已由 M3-M1 notify.process() 负责，本包不重复；
    - 只记录行为（回应/投递），不推断/不存心理标签；时段首版固定三档。

仅供标准库；MIT。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
from typing import Any, Callable, Mapping

from backend.m3 import content as _content
from backend.m3.content import quality_gate
from backend.m3.notify import DELIVERED

logger = logging.getLogger("m3.heartbeat")

# 时段三档（§8 开放问题 2：首版固定三档，hour 半开区间 [start, end)）
# 深夜 0–5 时不入档（休息时段，不主动开口）
DEFAULT_SLOTS: dict[str, tuple[int, int]] = {
    "early": (6, 12),      # 早  06:00–11:59
    "mid": (12, 18),       # 中  12:00–17:59
    "evening": (18, 24),   # 晚  18:00–23:59
}

# 频率档位（由密到稀；降频即 index+1，退避后更稀）
FREQUENCY_LADDER: tuple[str, ...] = ("daily", "every_2_days", "every_3_days", "sparse")
DEFAULT_FREQUENCY: str = "daily"

# 连续无响应天数阈值（§4.2 / §7：无响应 3 天降频）
DECAY_AFTER_DAYS: int = 3

# 定时循环默认检查间隔（秒）：每分钟检查一次 _should_run
DEFAULT_INTERVAL_SECONDS: float = 60.0


def slot_for(now: _dt.datetime, slots: Mapping[str, tuple[int, int]] | None = None) -> str | None:
    """返回 now 所在的时段档（early/mid/evening），深夜(0–5 时)不在任何档 → None。

    slots 为三档 hour 区间映射；本函数为纯函数，便于单测。
    """
    slots = slots or DEFAULT_SLOTS
    hour = now.hour
    for name, (start, end) in slots.items():
        if start <= hour < end:
            return name
    return None


class HeartbeatEngine:
    """M3-M2 心跳引擎：时段判定 → 候选生成 → 质量门 → process() 投递 → 无响应退避。

    依赖均可用关键字注入（便于测试替身）：
        notifier  M3-M1 ProactiveNotifier（或提供 .process() 的替身；必填）
        content   内容源（callable(now, ctx)->dict|None 或含 build_candidate 的对象；默认 build_candidate）
        now_fn    返回当前时刻（默认 datetime.now）
        bus       事件总线实例（备用；本包不新发布事件，proactive.* 已由 process() 负责）
        slots     三档时段配置（默认 DEFAULT_SLOTS）
        frequency 初始频率档（默认 daily）
        decay_after_days  连续无响应降频阈值（默认 3）
        interval_seconds  定时循环检查间隔（默认 60s）
    """

    def __init__(
        self,
        notifier: Any,
        content: Any = None,
        now_fn: Callable[[], _dt.datetime] | None = None,
        bus: Any | None = None,
        slots: Mapping[str, tuple[int, int]] | None = None,
        frequency: str = DEFAULT_FREQUENCY,
        decay_after_days: int = DECAY_AFTER_DAYS,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        quality_gate_fn: Callable[[Mapping | None], bool] | None = None,
        dormant: Any | None = None,
    ) -> None:
        self._notifier = notifier
        self._content = content if content is not None else _content.build_candidate
        self._now_fn: Callable[[], _dt.datetime] = now_fn or _dt.datetime.now
        self._bus = bus
        self._slots: dict[str, tuple[int, int]] = dict(slots or DEFAULT_SLOTS)
        self.frequency: str = str(frequency)
        self._decay_after_days: int = int(decay_after_days)
        self._interval_seconds: float = float(interval_seconds)
        self._quality_gate_fn = quality_gate_fn or quality_gate
        # DORMANT 订阅协调器（M3-M4 dormant.DormantCoordinator / 或提供 is_frozen() 的替身）；
        # 缺省 None → 不冻结检查（向后兼容），供 M3-M4 在生成候选前查询 is_frozen() 早退。
        self._dormant = dormant

        # ---- 无响应退避状态 ----
        self.no_response_streak: int = 0          # 连续无响应天数（可读、可断言）
        self._delivery_day: _dt.date | None = None  # 最近一次投递日（无投递则不追踪）
        self._settled_day: _dt.date | None = None   # 最近一次完成「逐日结算」的日期
        self._last_decay_day: _dt.date | None = None  # 最近一次降频日（防同一天重复降）

        # ---- 定时循环 ----
        self._running: bool = False
        self._task: asyncio.Task | None = None
        self._last_attempt: tuple[Any, ...] | None = None  # (date, slot) 防同一时段重复投递

    # =================================================================
    # ① 时段判定（§4.2 定时早/晚/时段；§8 首版固定三档）
    # =================================================================
    def _should_run(self, now: _dt.datetime) -> bool:
        """纯函数：now 是否落在任一活动时段档（早/中/晚）内。"""
        return slot_for(now, self._slots) is not None

    # =================================================================
    # ② 单次心跳：候选生成 → 质量门 → 投递（核心，可测，不 sleep）
    # =================================================================
    def _run_once(self, now: _dt.datetime) -> str | None:
        """执行一次心跳，返回投递状态（== DELIVERED）或 None（零投递/未命中时段）。

        零投递情形：未命中时段 / 内容源无素材 / 质量门不过（无素材宁可不说）。
        """
        # DORMANT 冻结（sec 4.4）：生成候选前早退，零投递（M3-M1 notify 仍是消费端最终闸门，双保险）
        if self._is_frozen():
            logger.info("[m3.heartbeat] DORMANT 冻结，心跳零投递: now=%s", now)
            return None
        if not self._should_run(now):
            return None
        candidate = self._build_candidate(now)
        if candidate is None or not self._quality_gate_fn(candidate):
            logger.info("[m3.heartbeat] 无素材或质量门不过，零投递: now=%s", now)
            return None
        status = self._notifier.process(candidate)
        if status == DELIVERED:
            self._record_delivery(now)
            self._maybe_decay(now)
        return status

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

    def _build_candidate(self, now: _dt.datetime) -> Mapping | None:
        """调用内容源生成候选；ctx 携带当前时段/频率/时刻供内容源（及未来 M1）使用。"""
        ctx: dict[str, Any] = {
            "slot": slot_for(now, self._slots),
            "frequency": self.frequency,
            "now": now,
        }
        return self._invoke_content(now, ctx)

    def _invoke_content(self, now: _dt.datetime, ctx: Mapping) -> Mapping | None:
        """内容源能力归一化：优先带 build_candidate 的对象，否则视为 callable。"""
        c = self._content
        if hasattr(c, "build_candidate"):
            return c.build_candidate(now, ctx)
        return c(now, ctx)

    # =================================================================
    # ③ 无响应退避（§4.2：连续 N 天无响应自动降频）
    # =================================================================
    def _record_delivery(self, now: _dt.datetime) -> None:
        """投递成功后记录：先结算跨天无响应天数，再记本日为投递日。"""
        self._rollover(now)
        self._delivery_day = now.date()

    def _rollover(self, now: _dt.datetime) -> None:
        """跨天结算：把「上次结算日至今」的日子按无响应累加进 streak。

        从未投递过则不追踪（无投递即无"有无响应"可判，不降频）。
        """
        day = now.date()
        if self._delivery_day is None:
            self._settled_day = day
            return
        if self._settled_day is None:
            self._settled_day = self._delivery_day
        if day <= self._settled_day:
            return
        span = (day - self._settled_day).days
        self.no_response_streak += span
        self._settled_day = day

    def _should_decay(self, now: _dt.datetime) -> bool:
        """无响应退避判定：连续无响应天数 >= 阈值 → 应降频。"""
        self._rollover(now)
        return self.no_response_streak >= self._decay_after_days

    def _apply_decay(self) -> str:
        """降频动作：frequency 在 FREQUENCY_LADDER 上降一档（daily→隔天→…→sparse）。"""
        try:
            idx = FREQUENCY_LADDER.index(self.frequency)
        except ValueError:
            idx = 0
        if idx < len(FREQUENCY_LADDER) - 1:
            self.frequency = FREQUENCY_LADDER[idx + 1]
        logger.info("[m3.heartbeat] 无响应降频：%s（连续无响应天数=%s）", self.frequency, self.no_response_streak)
        return self.frequency

    def _maybe_decay(self, now: _dt.datetime) -> bool:
        """若应降频且当日未降过 → 执行降频。返回是否真的发生了降频。"""
        if self._should_decay(now) and self._last_decay_day != now.date():
            self._apply_decay()
            self._last_decay_day = now.date()
            return True
        return False

    def note_user_response(self) -> None:
        """会话层在用户发言后调用：重置无响应 streak。

        内部方法：只重置行为计数，不发布任何事件（本包不新增事件契约）。
        回应率进 style_profile 的反向调节由 M3-M5 读取本计数完成。
        """
        now = self._now_fn()
        self.no_response_streak = 0
        self._settled_day = now.date()

    # =================================================================
    # ④ 后台定时循环（start/stop，asyncio 后台协程；借鉴 tasks.py 模式）
    # =================================================================
    def start(self) -> None:
        """启动后台定时循环（需在运行中的事件循环内调用）。"""
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.get_running_loop().create_task(self._loop())
        logger.info("[m3.heartbeat] 心跳定时循环已启动（interval=%ss）", self._interval_seconds)

    async def stop(self) -> None:
        """停止后台定时循环并等待协程退出。"""
        self._running = False
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("[m3.heartbeat] 心跳定时循环已停止")

    async def _loop(self) -> None:
        """周期性检查 _should_run；命中新时段则 _run_once。每分钟一拍。"""
        while self._running:
            now = self._now_fn()
            if self._should_run(now) and self._new_slot(now):
                try:
                    self._run_once(now)
                except Exception as e:  # noqa: BLE001  单拍失败不中断循环
                    logger.warning("[m3.heartbeat] 单拍执行失败，跳过: %s", e)
            await asyncio.sleep(self._interval_seconds)

    def _new_slot(self, now: _dt.datetime) -> bool:
        """同一时段（同一天 + 同档）只尝试一次，防每分钟重复投递。"""
        slot = slot_for(now, self._slots)
        key: tuple[Any, ...] = (now.date(), slot)
        if self._last_attempt == key:
            return False
        self._last_attempt = key
        return True
