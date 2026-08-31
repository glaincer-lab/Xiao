"""M6-M4 纪念锚点见证（MemorialWitness）：对丧失关系人物卡做月度见证式询问。

设计书 M6-growth.md §4.4（与 M1.6 联动）：
- 丧失关系建只读记忆（memorial_anchor）→ 见证式询问「想聊聊这段往事吗？」；
- 月度节奏（interval_days 默认 30 天）+ 可关闭（memorial_ask=False，关闭后不再问）；
- 3/7/30 天陪伴节奏由 M3 哀伤调度消费 grief_phase(entity) 驱动，本层提供只读委托。

存储边界（验收断言「复用 set_memorial_anchor/set_memorial_ask，不重复造存储」）：
- 锚点/开关状态一律复用 M1.5 人物卡存储（persona 的 memorial_anchor / memorial_ask）；
- 本层只额外落盘「上次询问日期」（persona 无此字段）到 root/memorial.json
  （root 缺省 ROOT/logs/m6，与 GrowthStore 同目录语义；测试注入隔离根）。

本层不发布事件（见证询问由调用方/M3 调度触发，无专属事件名，白名单不加）。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from backend.config import ROOT
from backend.memv1.persona import PersonaStore

# 默认见证式询问语（M6-growth.md §4.4 原文）
DEFAULT_PROMPT = "想聊聊这段往事吗？"


class MemorialWitness:
    """纪念锚点见证人：月度 + 可关闭的见证式询问。

    Args:
        persona_store: PersonaStore 实例（M1.5 人物卡 / M1.6 哀伤标签，供开关与节奏委托）。
        now_fn: 取当前时间的 callable（返回 date 或 datetime）；默认 date.today，
            测试注入可控时钟。
        interval_days: 两次见证询问的最小间隔天数（默认 30，月度）。
        root: 上次询问日期的落盘目录（缺省 ROOT/logs/m6；测试注入隔离根）。
        prompt_text: 询问语，缺省 DEFAULT_PROMPT。
    """

    def __init__(
        self,
        persona_store: PersonaStore,
        *,
        now_fn: Callable[[], date | datetime] | None = None,
        interval_days: int = 30,
        root: Path | str | None = None,
        prompt_text: str | None = None,
    ) -> None:
        self._persona = persona_store
        self._now_fn: Callable[[], date | datetime] = now_fn or date.today
        self._interval_days = max(1, int(interval_days))
        self._prompt_text = str(prompt_text or DEFAULT_PROMPT)
        self._root = ROOT / "logs" / "m6" if root is None else Path(root)
        self._path = self._root / "memorial.json"
        self._lock = threading.Lock()
        self._data = self._load()

    # ---- 见证式询问 ----

    def witness_prompt(self, name: str) -> str | None:
        """取见证式询问语；不应问时返回 None。

        前置条件（全满足才返回询问语，返回即视为「已问」并记录时间）：
        1. 人物卡存在且已打纪念锚点（memorial_anchor=True）；
        2. 见证式询问未关闭（memorial_ask=True）；
        3. 距上次询问 >= interval_days（或从未问过）。
        """
        card = self._persona.get_kinship_card(name)
        if card is None or not card.memorial_anchor:
            return None  # 非丧失关系不打扰
        if not card.memorial_ask:
            return None  # 已关闭见证式询问
        now = self._normalize_now()
        last = self._data["last_ask"].get(name)
        if last is not None:
            try:
                last_date = date.fromisoformat(last)
            except (TypeError, ValueError):
                last_date = None
            if last_date is not None and (now - last_date).days < self._interval_days:
                return None  # 间隔未满：30 天内只问一次
        self._set_last_ask(name, now)
        return self._prompt_text

    def disable(self, name: str) -> None:
        """关闭见证式询问（委托 persona.set_memorial_ask；人物卡不存在时报人类可读错）。"""
        self._persona.set_memorial_ask(name, False)

    def enable(self, name: str) -> None:
        """重新开启见证式询问（委托 persona.set_memorial_ask）。"""
        self._persona.set_memorial_ask(name, True)

    # ---- M3 哀伤调度联动（只读委托） ----

    def grief_phase(self, entity: str) -> str:
        """3/7/30 天陪伴节奏窗口（委托 persona.grief_phase，供 M3 调度消费）。"""
        return self._persona.grief_phase(entity)

    # ---- 内部 ----

    def _normalize_now(self) -> date:
        n = self._now_fn()
        if isinstance(n, datetime):
            return n.date()
        if isinstance(n, date):
            return n
        raise TypeError(f"now_fn 必须返回 date/datetime，收到 {type(n).__name__}")

    def _set_last_ask(self, name: str, when: date) -> None:
        with self._lock:
            self._data.setdefault("last_ask", {})[name] = when.isoformat()
            self._save()

    def _load(self) -> dict[str, Any]:
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("last_ask"), dict):
                last = {str(k): str(v) for k, v in data["last_ask"].items() if isinstance(v, str)}
                return {"last_ask": last}
            return {"last_ask": {}}
        except FileNotFoundError:
            return {"last_ask": {}}
        except Exception:  # noqa: BLE001 损坏文件不卡死启动
            print(f"[memorial] 纪念锚点数据文件损坏，已重置为空: {self._path}")
            return {"last_ask": {}}

    def _save(self) -> None:
        """原子写：先写临时文件，再 os.replace 覆盖（与 m6.growth 同模式）。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError as e:
            print(f"[memorial] 纪念锚点数据保存失败: {e}")


__all__ = ["MemorialWitness", "DEFAULT_PROMPT"]
