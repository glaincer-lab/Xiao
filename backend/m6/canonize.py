"""M6 §4.1 双源入册制（backend/m6/canonize.py）。

「办成一次事」候选识别 + 用户册封 + 冷却 + 周预算 + Aging 提权：
- 候选识别 schema（写死）：订阅 `task.completed` + `vision.feedback`（三态 接受/不接受/部分，
  正向=接受），双源齐备才识别为候选（"任务完成 + S6 反馈正向 = 办成一次事"），缺一或负向不生成；
- 候选不弹通知、不进正式记录：仅进待册队列，由 `poll_ask()` 在下次闲聊时返回一句轻提提示语
  （本层只返回字符串，不主动执行/推送）；
- 用户确认（好/记上）→ 入册 `GrowthStore`（canon=True），能力类入小二轨（capability_event 必填、
  不可编造，来自任务结果中的真实凭证）；拒绝（不用）→ 丢弃 + 同类事件 90 天冷却（冷却键=事件类型）；
- 册封询问周 ≤1 次（每候选分项上限），并受全局周询问预算 ≤5 次/周约束（优先级最低，冲突顺延下周）；
- Aging Policy（v4.1.1 终审）：候选连续 3 周被顺延，第 4 周无条件提权至与 M1 澄清同级（强制输出，
  防低优先级在高压期饥饿死锁）。

事件（全部在 backend/event_bus.py 白名单内，不新增）：
- 订阅：task.completed / vision.feedback
- 发布：growth.candidate（{事件,能力凭证}）候选识别时 / growth.canonized（{记录id,轨别}）入册后

持久化：待册队列/冷却表/周询问计数存 `<store root>/canonizer_state.json`（原子写），
重启后冷却与候选不丢失（store root 注入即测试隔离，默认 ROOT/logs/m6）。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.config import ROOT
from backend.event_bus import bus as default_bus
from backend.m6.growth import GrowthStore

# 轻提提示语（设计书 §4.1 话术原文）；本层只返回字符串，不主动执行
PROMPT_TEXT = "刚才那事成了，要记进咱们的本子里吗？"

# 用户回复解析词表（确认 → 入册；拒绝 → 丢弃+冷却）
CONFIRM_WORDS = ("好", "好的", "记上", "记下来", "记吧", "记", "可以", "行", "嗯", "好呀", "要")
REJECT_WORDS = ("不用", "不要", "算了", "不需要", "不了", "不用了", "拉倒", "不记", "别")

# vision.feedback 三态中视为「正向=接受」的值（"不接受"/"部分"不在此列）
_POSITIVE_WORDS = frozenset(
    {"接受", "accept", "accepted", "positive", "approve", "approved", "yes", "true", "好"}
)


class Canonizer:
    """双源入册制业务层：候选识别 → 轻提 → 确认入册 / 拒绝冷却。

    Args:
        bus: 事件总线（默认 backend/event_bus.py 单例；测试注入独立 EventBus 隔离）。
        store: GrowthStore 实例（默认 ROOT/logs/m6；测试注入 root=ROOT/.tmp/xxx 隔离）。
        now_fn: 时间源（默认 time.time；测试注入可控时钟模拟周/冷却流逝）。
        budget_hook: 返回「本周全局已用询问次数」的 callable（默认 None → 用本模块内部计数）。
            全局周询问预算（≤5 次/周）与 M1 澄清/M2 印证共享，由上层统一计数后经此查询。
        budget_per_week: 全局周询问预算上限（默认 5，EVENT_REGISTRY §5 写死）。
        cooldown_days: 拒绝后同类事件冷却天数（默认 90，设计书写死）。
        match_window_seconds: task.completed 与 vision.feedback 的配对时间窗（默认 600 秒）。
        aging_weeks: 连续顺延周数阈值，达到后第 4 周提权（默认 3，设计书写死）。
        state_path: 状态文件路径（默认 <store root>/canonizer_state.json）。
    """

    def __init__(
        self,
        bus: Any | None = None,
        store: GrowthStore | None = None,
        *,
        now_fn: Callable[[], float] | None = None,
        budget_hook: Callable[[], int] | None = None,
        budget_per_week: int = 5,
        cooldown_days: int = 90,
        match_window_seconds: int = 600,
        aging_weeks: int = 3,
        state_path: Path | str | None = None,
    ) -> None:
        if budget_per_week < 1:
            raise ValueError("budget_per_week 至少为 1（全局周询问预算）")
        if cooldown_days < 0:
            raise ValueError("cooldown_days 不能为负")
        if aging_weeks < 1:
            raise ValueError("aging_weeks 至少为 1（连续顺延周数阈值）")
        self.bus = bus if bus is not None else default_bus
        self.store = store if store is not None else GrowthStore()
        self.now_fn = now_fn if now_fn is not None else time.time
        self.budget_hook = budget_hook
        self.budget_per_week = int(budget_per_week)
        self.cooldown_days = int(cooldown_days)
        self.match_window_seconds = float(match_window_seconds)
        self.aging_weeks = int(aging_weeks)
        self._explicit_state_path = Path(state_path) if state_path is not None else None

        # 会话级配对窗口（task.completed 等待 vision.feedback 正向，仅内存，跨重启不保留）
        self._tasks: list[dict[str, Any]] = []
        self._subs: list[Callable[[], None]] = []

        state = self._load_state()
        self._pending: list[dict[str, Any]] = state["pending"]
        self._cooldowns: dict[str, float] = state["cooldowns"]
        self._weekly: dict[str, int] = state["weekly"]

    # ------------------------------------------------------------------ 生命周期

    def start(self) -> None:
        """订阅事件（幂等）。测试注入独立 bus 时互不干扰。"""
        if self._subs:
            return
        self._subs.append(self.bus.on("task.completed", self._on_task_completed))
        self._subs.append(self.bus.on("vision.feedback", self._on_vision_feedback))

    def close(self) -> None:
        """取消订阅（幂等）。"""
        for unsub in self._subs:
            try:
                unsub()
            except Exception:  # noqa: BLE001 取消订阅失败不阻断
                pass
        self._subs = []

    # ------------------------------------------------------------------ 候选识别（双源）

    def _on_task_completed(self, payload: dict[str, Any]) -> None:
        now_ts = self._now()
        self._tasks.append(
            {
                "task_id": str(payload.get("task_id") or ""),
                "result": payload.get("result"),
                "ts": now_ts,
            }
        )
        self._prune_tasks(now_ts)

    def _on_vision_feedback(self, payload: dict[str, Any]) -> None:
        """S6 反馈三态：正向（接受）且窗口内有未配对的 task.completed → 识别候选。"""
        if not self._is_positive_feedback(payload):
            return
        now_ts = self._now()
        self._prune_tasks(now_ts)
        if not self._tasks:
            return
        task = self._tasks.pop(0)  # 最老任务优先配对
        self._ingest_candidate(task, now_ts)

    def _ingest_candidate(self, task: dict[str, Any], now_ts: float) -> None:
        milestone, capability_event, event_type = self._extract(task)
        # 同类 90 天冷却：冷却期内同类候选不再生成/轻提
        if self._on_cooldown(event_type, now_ts):
            return
        kind = "agent" if capability_event else "user"
        cand: dict[str, Any] = {
            "id": os.urandom(6).hex(),
            "milestone": milestone,
            "capability_event": capability_event,
            "event_type": event_type,
            "kind": kind,
            "task_id": task["task_id"],
            "created_ts": now_ts,
            "asked_ts": None,
            "asked_week": None,
            "status": "pending",          # pending → asked（轻提后）
            "deferred_weeks": 0,          # 连续被预算顺延的周数（Aging）
            "deferred_week": None,        # 最近一次顺延的周键（同周只累计一次）
        }
        self._pending.append(cand)
        self._save()
        self.bus.emit("growth.candidate", {"事件": milestone, "能力凭证": capability_event})

    @staticmethod
    def _extract(task: dict[str, Any]) -> tuple[str, str | None, str]:
        """从 task.completed 提取 milestone / 能力凭证 / 事件类型（冷却键）。"""
        task_id = task.get("task_id") or ""
        result = task.get("result")
        if isinstance(result, dict):
            milestone = result.get("milestone") or result.get("summary")
            if not milestone:
                milestone = f"完成任务 {task_id}".strip() if task_id else "完成一次任务"
            capability = result.get("capability_event")
            event_type = result.get("event_type") or result.get("category") or capability or "task"
        else:
            milestone = f"完成任务 {task_id}".strip() if task_id else "完成一次任务"
            capability = None
            event_type = "task"
        cap_str = str(capability).strip() if capability else None
        return str(milestone).strip(), (cap_str or None), str(event_type).strip() or "task"

    def _prune_tasks(self, now_ts: float) -> None:
        self._tasks = [t for t in self._tasks if now_ts - t["ts"] <= self.match_window_seconds]

    @staticmethod
    def _is_positive_feedback(payload: dict[str, Any]) -> bool:
        """vision.feedback 三态（接受/不接受/部分），正向=接受。"""
        if not isinstance(payload, dict):
            return False
        v = (
            payload.get("三态")
            or payload.get("status")
            or payload.get("result")
            or payload.get("feedback")
        )
        if v is None:
            return False
        return str(v).strip().lower() in _POSITIVE_WORDS

    # ------------------------------------------------------------------ 轻提（poll_ask）

    def poll_ask(self, now: float | None = None) -> str | None:
        """下次闲聊时的册封轻提：返回提示语字符串（不主动执行），无候选/预算冲突则 None。

        规则：Aging 提权候选（连续 3 周被顺延）无条件优先输出；其余候选受
        分项周 ≤1 次与全局周预算 ≤5 次约束，冲突则顺延（deferred_weeks+1）下周再问。
        """
        now_ts = self._now(now)
        week = self._week_key(now_ts)
        self._prune_tasks(now_ts)
        cands = [c for c in self._pending if not self._on_cooldown(c["event_type"], now_ts)]
        if not cands:
            return None
        # 分项上限：每候选每周最多轻提 1 次
        eligible = [c for c in cands if c.get("asked_week") != week]
        if not eligible:
            return None
        # Aging 提权：连续 aging_weeks 周被顺延 → 无条件输出（强制消耗配额）
        aged = [c for c in eligible if int(c.get("deferred_weeks", 0)) >= self.aging_weeks]
        if aged:
            target = sorted(aged, key=lambda c: c.get("created_ts", 0))[0]
        else:
            used = self._budget_used(week)
            if used >= self.budget_per_week:
                self._defer(eligible, week)
                return None
            target = sorted(eligible, key=lambda c: c.get("created_ts", 0))[0]
        target["asked_ts"] = now_ts
        target["asked_week"] = week
        target["status"] = "asked"
        target["deferred_weeks"] = 0
        target["deferred_week"] = None
        self._weekly[week] = self._weekly.get(week, 0) + 1
        self._save()
        return PROMPT_TEXT

    def _defer(self, cands: list[dict[str, Any]], week: str) -> None:
        """预算冲突顺延：同周只累计一次，跨周才 +1（Aging 按周计）。"""
        changed = False
        for c in cands:
            if c.get("deferred_week") != week:
                c["deferred_weeks"] = int(c.get("deferred_weeks", 0)) + 1
                c["deferred_week"] = week
                changed = True
        if changed:
            self._save()

    def _budget_used(self, week: str) -> int:
        """本周全局已用询问次数：有 budget_hook 用全局计数（与 M1/M2 共享），否则内部计数。"""
        if self.budget_hook is not None:
            try:
                return max(0, int(self.budget_hook()))
            except Exception:  # noqa: BLE001 预算查询失败按 0 处理，不阻断
                return 0
        return self._weekly.get(week, 0)

    # ------------------------------------------------------------------ 用户回应

    def handle_user_reply(self, reply: str | None, candidate_id: str | None = None) -> dict[str, Any]:
        """解析用户对轻提的回应：确认（好/记上…）→ 入册；拒绝（不用…）→ 丢弃+冷却。"""
        text = str(reply or "").strip()
        if not text:
            return {"ok": False, "reason": "回复为空"}
        if text in CONFIRM_WORDS:
            return self.confirm(candidate_id)
        if text in REJECT_WORDS:
            return self.reject(candidate_id)
        return {"ok": False, "reason": f"未识别的回复 {text!r}（确认：好/记上；拒绝：不用）"}

    def confirm(self, candidate_id: str | None = None) -> dict[str, Any]:
        """用户确认入册（canon=True）：能力类入小二轨，其余入用户轨；广播 growth.canonized。"""
        cand = self._pick_candidate(candidate_id)
        if not cand:
            return {"ok": False, "reason": "没有待册封的候选"}
        if cand["kind"] == "agent":
            rec = self.store.add_agent_record(
                cand["milestone"], capability_event=cand["capability_event"], canon=True
            )
            track = "agent"
        else:
            rec = self.store.add_user_record(cand["milestone"], source="自动识别", canon=True)
            track = "user"
        self._drop(cand["id"])
        self.bus.emit("growth.canonized", {"记录id": rec["id"], "轨别": track})
        return {
            "ok": True,
            "record_id": rec["id"],
            "track": track,
            "milestone": cand["milestone"],
            "candidate_id": cand["id"],
        }

    def reject(self, candidate_id: str | None = None, now: float | None = None) -> dict[str, Any]:
        """用户拒绝：丢弃候选 + 同类事件 90 天冷却（冷却键=事件类型）。"""
        cand = self._pick_candidate(candidate_id)
        if not cand:
            return {"ok": False, "reason": "没有可拒绝的候选"}
        until = self._now(now) + self.cooldown_days * 86400.0
        self._cooldowns[cand["event_type"]] = until
        self._drop(cand["id"])
        return {
            "ok": True,
            "event_type": cand["event_type"],
            "cooldown_until": until,
            "candidate_id": cand["id"],
        }

    def _pick_candidate(self, candidate_id: str | None = None) -> dict[str, Any] | None:
        if candidate_id:
            return self._pending_candidate(candidate_id)
        asked = [c for c in self._pending if c.get("status") == "asked"]
        if asked:
            return sorted(asked, key=lambda c: c.get("asked_ts", 0), reverse=True)[0]
        if self._pending:
            return sorted(self._pending, key=lambda c: c.get("created_ts", 0))[0]
        return None

    def _drop(self, candidate_id: str) -> None:
        self._pending = [c for c in self._pending if c["id"] != candidate_id]
        self._save()

    # ------------------------------------------------------------------ 查询（只读快照）

    def pending_candidates(self) -> list[dict[str, Any]]:
        """待册候选（拷贝）。"""
        return [dict(c) for c in self._pending]

    def cooldowns(self) -> dict[str, float]:
        """同类事件冷却表：{事件类型: 冷却截止时间戳}（拷贝）。"""
        return dict(self._cooldowns)

    def weekly_asks(self) -> dict[str, int]:
        """本模块周询问计数：{周键: 次数}（拷贝）。"""
        return dict(self._weekly)

    def _pending_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        for c in self._pending:
            if c["id"] == candidate_id:
                return c
        return None

    # ------------------------------------------------------------------ 内部

    def _now(self, now: float | None = None) -> float:
        return float(now) if now is not None else float(self.now_fn())

    @staticmethod
    def _week_key(ts: float) -> str:
        iso = datetime.fromtimestamp(ts, tz=timezone.utc).isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    def _on_cooldown(self, event_type: str, now_ts: float) -> bool:
        until = self._cooldowns.get(event_type)
        return bool(until and until > now_ts)

    # ------------------------------------------------------------------ 持久化（<store root>/canonizer_state.json）

    def _state_path(self) -> Path:
        if self._explicit_state_path is not None:
            return self._explicit_state_path
        root = getattr(self.store, "root", None)
        base = Path(root) if root is not None else ROOT / "logs" / "m6"
        return base / "canonizer_state.json"

    def _load_state(self) -> dict[str, Any]:
        empty = {"pending": [], "cooldowns": {}, "weekly": {}}
        try:
            with self._state_path().open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return empty
            return {
                "pending": [c for c in data.get("pending", []) if isinstance(c, dict)],
                "cooldowns": {str(k): float(v) for k, v in (data.get("cooldowns") or {}).items()},
                "weekly": {str(k): int(v) for k, v in (data.get("weekly") or {}).items()},
            }
        except FileNotFoundError:
            return empty
        except Exception:  # noqa: BLE001 状态文件损坏 → 重置为空（与 GrowthStore 同容错）
            print(f"[m6] canonizer 状态文件损坏，已重置为空: {self._state_path()}")
            return empty

    def _save(self) -> None:
        """原子写：tmp + os.replace（与 GrowthStore 同模式）。"""
        try:
            p = self._state_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(
                    {"pending": self._pending, "cooldowns": self._cooldowns, "weekly": self._weekly},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(tmp, p)
        except OSError as e:
            print(f"[m6] canonizer 状态保存失败: {e}")


__all__ = ["Canonizer", "PROMPT_TEXT", "CONFIRM_WORDS", "REJECT_WORDS"]
