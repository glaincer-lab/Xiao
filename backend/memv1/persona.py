"""M1-F 人设/世界观/人物卡/哀伤标签/惯例画像（内容资产层）。

纯数据配置 + 读取接口，**无 LLM**，仅标准库。与 `backend/memory.py`（v3）、
`backend/memv4.py`（v4 基础层）并存且独立。本模块负责记忆的**内容资产**——
记录「小二」是谁（人设卡/世界观），以及关于用户认识的人（亲友人物卡）、
用户的丧失与习惯（哀伤标签/惯例画像）。

数据目录（可编辑）：
- `persona/persona.json`          人设卡（身份/语气/称呼/边界），带版本历史可回退
- `persona/kinship.json`          亲友人物卡（M1.5）
- `persona/grief.json`            哀伤标签（M1.6，只增不推断）
- `persona/habit.json`            惯例画像与重建模式（M1.7）
- `persona/lorebook/*.json`       世界观条目（触发式注入，预置少量）

对应规格（docs/ROADMAP.md §4.M1.4-1.7）：
- M1.4 人设卡与世界观（Lorebook 式）：`persona/` 可编辑，带版本历史可回退；世界观条目触发式注入。
- M1.5 关系人物卡：亲友是记忆一等公民，名字/关系/事件/近期动态；纪念锚点——
  丧失关系可建「只读记忆」，见证式询问月级 + 可关闭。
- M1.6 哀伤标签：重大丧失打标；**入口以用户明说为主**——本模块只提供显式打标
  入口 `add_grief_tag`，**没有任何「推断打标」函数**（只增不推断）；3/7/30 天
  陪伴节奏以 `grief_phase`（只读）给出，供 M2/M3 心跳日程消费。
- M1.7 惯例画像与重建模式：作息/常用参数画像 → 偏离惯例确认；重大转变触发
  重建模式——旧惯例标失效、新惯例学习中。

契约（写死）：
    load_persona() -> dict
    KinshipCard                                # name/relation/events/recent
    add_grief_tag(entity: str) -> None
    habit_profile() -> dict
    inject_lorebook(trigger: str) -> str

MIT。
"""
from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backend.config import ROOT


# --------------------------------------------------------------------------- #
# 默认人设卡（首次访问落到 persona/persona.json 的初始值）
# --------------------------------------------------------------------------- #
DEFAULT_PERSONA: dict[str, Any] = {
    "version": 1,
    "identity": "小二",
    "tone": "温和、简洁、不评判；绝不与用户对质，也不拿记忆跟用户翻旧账",
    "addressing": "老板",
    "boundaries": [
        "不做心理诊断",
        "不翻旧账",
        "不主动否定用户决定",
    ],
    "history": [],
}


@dataclass
class KinshipCard:
    """亲友人物卡（M1.5）。

    四要素（写死）：name / relation / events / recent。
    扩展字段用于纪念锚点：丧失关系可建「只读记忆」（readonly），
    见证式询问为月级（memorial_ask），可关闭。
    """

    name: str
    relation: str
    events: list[dict[str, Any]] = field(default_factory=list)  # [{date,title,emotion}]
    recent: str = ""
    # ---- 纪念锚点扩展（M1.5）----
    memorial_anchor: bool = False   # 丧失关系 → 纪念锚点
    memorial_ask: bool = True       # 见证式询问开关（月级），可关闭
    readonly: bool = False          # 只读记忆（丧失关系不可编辑）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_json(path: Path, default: Any) -> Any:
    """从磁盘读 JSON；缺失 / 损坏时返回 default（不让坏文件卡死启动）。"""
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:  # noqa: BLE001
        print(f"[persona] 数据文件损坏，已重置为默认: {path}")
        return default


def _save_json(path: Path, data: Any) -> None:
    """原子写（tmp + os.replace，与 memory.py 同模式）。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        print(f"[persona] 保存失败: {path}: {e}")


def _deepcopy(obj: Any) -> Any:
    return copy.deepcopy(obj)


class PersonaStore:
    """M1-F 内容资产层：人设/世界观/人物卡/哀伤标签/惯例画像 的存取器。

    所有数据落在 `root/persona/`（root 缺省为项目根 ROOT）。
    读写均线程安全；返回结构均为深拷贝，调用方改动不影响内存态。
    提供模块级默认实例 `_store`，请直接使用契约函数；测试可构造临时
    `PersonaStore(root=<tmp>)` 并替换 `persona._store` 实现隔离。
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = ROOT if root is None else Path(root)
        self._persona_dir = self._root / "persona"
        self._persona_path = self._persona_dir / "persona.json"
        self._kinship_path = self._persona_dir / "kinship.json"
        self._grief_path = self._persona_dir / "grief.json"
        self._habit_path = self._persona_dir / "habit.json"
        self._lorebook_dir = self._persona_dir / "lorebook"
        self._lock = threading.RLock()  # 可重入：更新/回退/重建会在持锁时读快照

    # ===================================================================== #
    # M1.4 人设卡 + 世界观（触发式注入）
    # ===================================================================== #

    def load_persona(self) -> dict[str, Any]:
        """读取当前人设卡；文件缺失时返回默认人设卡（并落盘）。"""
        with self._lock:
            data = _load_json(self._persona_path, None)
            if not isinstance(data, dict):
                data = copy.deepcopy(DEFAULT_PERSONA)
                _save_json(self._persona_path, data)
            # 补齐缺失字段，保证契约字段存在
            merged = copy.deepcopy(DEFAULT_PERSONA)
            merged.update(data)
            merged.setdefault("history", [])
            return _deepcopy(merged)

    def update_persona(self, fields: dict[str, Any]) -> dict[str, Any]:
        """更新人设卡，bump 版本并把旧版快照压入 history（可回退）。

        Args:
            fields: 允许修改 identity/tone/addressing/boundaries；忽略 version/history。
        Returns:
            更新后的人设卡（含新 version）。
        """
        with self._lock:
            current = self.load_persona()
            # 当前版本拷入历史（不带 history 自身，避免嵌套）
            snapshot = {
                "version": current["version"],
                "identity": current["identity"],
                "tone": current["tone"],
                "addressing": current["addressing"],
                "boundaries": copy.deepcopy(current["boundaries"]),
            }
            history = current.get("history", [])
            history = copy.deepcopy(history)
            history.append(snapshot)
            for key in list(fields.keys()):
                if key in ("version", "history"):
                    fields.pop(key)
            current.update(fields)
            current["version"] = int(current["version"]) + 1
            current["history"] = history
            _save_json(self._persona_path, current)
            return _deepcopy(current)

    def rollback_persona(self, version: int) -> dict[str, Any]:
        """回退到指定历史版本。

        Args:
            version: history 中某个快照的 version。
        Returns:
            回退后的人设卡（version 继续递增，不回退编号）。
        Raises:
            ValueError: 目标版本不存在或已是当前版本。
        """
        with self._lock:
            current = self.load_persona()
            if version == int(current["version"]):
                raise ValueError(f"已是版本 {version}，无需回退")
            history = copy.deepcopy(current.get("history", []))
            target = next((h for h in history if int(h["version"]) == int(version)), None)
            if target is None:
                raise ValueError(f"找不到历史版本 {version}")
            # 先把当前状态压入历史（回退可再回退）
            history.append({
                "version": current["version"],
                "identity": current["identity"],
                "tone": current["tone"],
                "addressing": current["addressing"],
                "boundaries": copy.deepcopy(current["boundaries"]),
            })
            restored = {
                "version": int(current["version"]) + 1,
                "identity": target["identity"],
                "tone": target["tone"],
                "addressing": target["addressing"],
                "boundaries": copy.deepcopy(target["boundaries"]),
                "history": history,
            }
            _save_json(self._persona_path, restored)
            return _deepcopy(restored)

    # ------------------------------------------------------------------- #
    # 世界观（Lorebook 式，触发式注入）
    # ------------------------------------------------------------------- #

    def lorebook_entries(self) -> list[dict[str, Any]]:
        """读取 `persona/lorebook/*.json` 中全部世界观条目。"""
        entries: list[dict[str, Any]] = []
        if not self._lorebook_dir.is_dir():
            return entries
        for path in sorted(self._lorebook_dir.glob("*.json")):
            data = _load_json(path, [])
            if isinstance(data, list):
                for e in data:
                    if isinstance(e, dict) and str(e.get("content", "")).strip():
                        entries.append(e)
        return entries

    def inject_lorebook(self, trigger: str) -> str:
        """按触发词注入世界观。

        Args:
            trigger: 触发意图（通常为当前输入/用户话语片段）。
        Returns:
            命中条目内容按文件顺序拼接；未命中返回空串。
        """
        if not trigger:
            return ""
        trigger = str(trigger).strip()
        matched: list[str] = []
        for entry in self.lorebook_entries():
            triggers = entry.get("triggers", [])
            if not isinstance(triggers, list):
                continue
            if any(isinstance(t, str) and t and t in trigger for t in triggers):
                content = str(entry.get("content", "")).strip()
                if content:
                    matched.append(content)
        return "\n".join(matched)

    # ===================================================================== #
    # M1.5 亲友人物卡（增/改/查）
    # ===================================================================== #

    def add_kinship_card(
        self,
        name: str,
        relation: str,
        events: list[dict[str, Any]] | None = None,
        recent: str = "",
        *,
        memorial_anchor: bool = False,
        memorial_ask: bool = True,
        readonly: bool = False,
    ) -> KinshipCard:
        """新增一张亲友人物卡（name 唯一）。

        Raises:
            ValueError: name/relation 为空，或 name 已存在。
        """
        name = str(name or "").strip()
        relation = str(relation or "").strip()
        if not name:
            raise ValueError("人物卡 name 不能为空")
        if not relation:
            raise ValueError("人物卡 relation 不能为空")
        events = [dict(e) for e in (events or []) if isinstance(e, dict)]
        card = KinshipCard(
            name=name,
            relation=relation,
            events=events,
            recent=str(recent or ""),
            memorial_anchor=bool(memorial_anchor),
            memorial_ask=bool(memorial_ask),
            readonly=bool(readonly),
        )
        with self._lock:
            cards = self._kinship_load()
            if any(c["name"] == name for c in cards):
                raise ValueError(f"人物卡已存在: {name}")
            cards.append(card.to_dict())
            _save_json(self._kinship_path, cards)
            return card

    def update_kinship_card(self, name: str, **fields: Any) -> KinshipCard:
        """更新人物卡（仅允许 relation/events/recent）。

        Raises:
            ValueError: 不存在、只读记忆禁止编辑、或字段名不被允许/改 name。
        """
        name = str(name or "").strip()
        allowed = {"relation", "events", "recent"}
        for key in fields:
            if key not in allowed:
                raise ValueError(f"人物卡不允许修改字段: {key!r}")
        with self._lock:
            cards = self._kinship_load()
            target = next((c for c in cards if c["name"] == name), None)
            if target is None:
                raise ValueError(f"人物卡不存在: {name}")
            if target["readonly"]:
                raise ValueError(f"只读记忆，不允许编辑: {name}")
            if "relation" in fields:
                rel = str(fields["relation"] or "").strip()
                if not rel:
                    raise ValueError("relation 不能为空")
                target["relation"] = rel
            if "events" in fields:
                evs = fields["events"]
                if not isinstance(evs, list):
                    raise TypeError("events 必须是 list[dict]")
                target["events"] = [dict(e) for e in evs if isinstance(e, dict)]
            if "recent" in fields:
                target["recent"] = str(fields["recent"] or "")
            _save_json(self._kinship_path, cards)
            return self._kinship_from_dict(target)

    def get_kinship_card(self, name: str) -> KinshipCard | None:
        """按 name 查询人物卡；不存在返回 None。"""
        name = str(name or "").strip()
        with self._lock:
            for c in self._kinship_load():
                if c["name"] == name:
                    return self._kinship_from_dict(c)
            return None

    def list_kinship_cards(self) -> list[KinshipCard]:
        """列出全部人物卡（按 name 排序）。"""
        with self._lock:
            cards = [self._kinship_from_dict(c) for c in self._kinship_load()]
            return sorted(cards, key=lambda k: k.name)

    def set_memorial_anchor(self, name: str, enabled: bool) -> KinshipCard:
        """设置/取消纪念锚点（丧失关系 → 设为只读记忆）。"""
        name = str(name or "").strip()
        with self._lock:
            cards = self._kinship_load()
            target = next((c for c in cards if c["name"] == name), None)
            if target is None:
                raise ValueError(f"人物卡不存在: {name}")
            target["memorial_anchor"] = bool(enabled)
            if enabled:
                target["readonly"] = True
                target["memorial_ask"] = True
            _save_json(self._kinship_path, cards)
            return self._kinship_from_dict(target)

    def set_memorial_ask(self, name: str, enabled: bool) -> KinshipCard:
        """关闭/开启见证式询问（月级）。允许作用于只读记忆。"""
        name = str(name or "").strip()
        with self._lock:
            cards = self._kinship_load()
            target = next((c for c in cards if c["name"] == name), None)
            if target is None:
                raise ValueError(f"人物卡不存在: {name}")
            target["memorial_ask"] = bool(enabled)
            _save_json(self._kinship_path, cards)
            return self._kinship_from_dict(target)

    def _kinship_load(self) -> list[dict[str, Any]]:
        data = _load_json(self._kinship_path, [])
        return [c for c in data if isinstance(c, dict) and str(c.get("name", "")).strip()]

    @staticmethod
    def _kinship_from_dict(d: dict[str, Any]) -> KinshipCard:
        return KinshipCard(
            name=str(d.get("name", "")),
            relation=str(d.get("relation", "")),
            events=[dict(e) for e in d.get("events", []) if isinstance(e, dict)],
            recent=str(d.get("recent", "")),
            memorial_anchor=bool(d.get("memorial_anchor", False)),
            memorial_ask=bool(d.get("memorial_ask", True)),
            readonly=bool(d.get("readonly", False)),
        )

    # ===================================================================== #
    # M1.6 哀伤标签（只增不推断）
    # ===================================================================== #

    def add_grief_tag(self, entity: str) -> None:
        """为重大丧失打标。**唯一的打标入口**——只由用户明说触发。

        只增不推断：本模块没有任何推导函数会自动生成标签；重复打标幂等
        （同一实体只记一条），且不提供删除接口（标签只增不减）。
        """
        entity = str(entity or "").strip()
        if not entity:
            raise ValueError("哀伤标签 entity 不能为空")
        with self._lock:
            tags = self._grief_load()
            if any(t["entity"] == entity for t in tags):
                return  # 幂等：重复打标不产生重复条目
            tags.append({
                "entity": entity,
                "ts": datetime.now().isoformat(),
                "source": "explicit",  # 入口以用户明说为主
            })
            _save_json(self._grief_path, tags)

    def grief_tags(self) -> list[dict[str, Any]]:
        """读取全部哀伤标签（每条为拷贝，外部改动不影响内存态）。"""
        with self._lock:
            return _deepcopy(self._grief_load())

    def grief_phase(self, entity: str, now: date | datetime | None = None) -> str:
        """只读：给出某标签当前所处的 3/7/30 天陪伴节奏窗口（供 M2/M3 消费）。

        Returns:
            "d3"（3 天内）/ "d7" / "d30" / "past"（30 天后）；未打标返回空串。
        """
        entity = str(entity or "").strip()
        tag = next((t for t in self._grief_load() if t["entity"] == entity), None)
        if tag is None:
            return ""
        if isinstance(now, datetime):
            now = now.date()
        today = now if isinstance(now, date) else date.today()
        try:
            ts_date = datetime.fromisoformat(tag["ts"]).date()
        except (KeyError, TypeError, ValueError):
            return "d3"
        delta = (today - ts_date).days
        if delta < 3:
            return "d3"
        if delta < 7:
            return "d7"
        if delta < 30:
            return "d30"
        return "past"

    def _grief_load(self) -> list[dict[str, Any]]:
        data = _load_json(self._grief_path, [])
        return [t for t in data if isinstance(t, dict) and str(t.get("entity", "")).strip()]

    # ===================================================================== #
    # M1.7 惯例画像与重建模式
    # ===================================================================== #

    def habit_profile(self) -> dict[str, Any]:
        """读取惯例画像。

        Returns:
            {"mode": "normal"|"rebuild", "rebuild_reason": str, "habits": [...]}
        """
        with self._lock:
            data = _load_json(self._habit_path, None)
            if not isinstance(data, dict):
                return {"mode": "normal", "rebuild_reason": "", "habits": []}
            habits = data.get("habits", [])
            if not isinstance(habits, list):
                habits = []
            return {
                "mode": str(data.get("mode", "normal")),
                "rebuild_reason": str(data.get("rebuild_reason", "")),
                "habits": _deepcopy([h for h in habits if isinstance(h, dict)]),
            }

    def add_habit(self, content: str, category: str = "一般") -> dict[str, Any]:
        """新增一条惯例（作息/常用参数/操作模式等）。

        重建模式下新增的惯例状态为 learning（学习中），否则为 active。
        """
        content = str(content or "").strip()
        if not content:
            raise ValueError("惯例 content 不能为空")
        with self._lock:
            data = self.habit_profile()
            mode = data["mode"]
            habits = data["habits"]
            habit: dict[str, Any] = {
                "id": uuid.uuid4().hex[:8],
                "content": content,
                "category": str(category or "一般"),
                "status": "learning" if mode == "rebuild" else "active",
                "since": date.today().isoformat(),
            }
            habits.append(habit)
            _save_json(self._habit_path, {
                "mode": mode,
                "rebuild_reason": data["rebuild_reason"],
                "habits": habits,
            })
            return _deepcopy(habit)

    def enter_rebuild_mode(self, reason: str) -> dict[str, Any]:
        """进入重建模式：重大转变触发，把既有 active 惯例标为 expired，
        并开始学习新惯例（后续新增惯例为 learning）。"""
        reason = str(reason or "").strip()
        with self._lock:
            data = self.habit_profile()
            habits = data["habits"]
            for h in habits:
                if h.get("status") == "active":
                    h["status"] = "expired"  # 旧惯例标失效
            _save_json(self._habit_path, {
                "mode": "rebuild",
                "rebuild_reason": reason,
                "habits": habits,
            })
            return self.habit_profile()

    def exit_rebuild_mode(self) -> dict[str, Any]:
        """退出重建模式：把 learning 惯例落为 active，回到正常态。"""
        with self._lock:
            data = self.habit_profile()
            habits = data["habits"]
            for h in habits:
                if h.get("status") == "learning":
                    h["status"] = "active"
            _save_json(self._habit_path, {
                "mode": "normal",
                "rebuild_reason": "",
                "habits": habits,
            })
            return self.habit_profile()

    # ===================================================================== #
    # 清空画像（隐私删除入口，幂等）
    # ===================================================================== #

    def clear(self) -> None:
        """清空用户画像（幂等）：人设卡重置为默认、亲友卡/哀伤标签清空、惯例画像重置。

        世界观（lorebook/*.json）为预置内容资产，不随画像清空。
        """
        with self._lock:
            _save_json(self._persona_path, copy.deepcopy(DEFAULT_PERSONA))
            _save_json(self._kinship_path, [])
            _save_json(self._grief_path, [])
            _save_json(self._habit_path, {"mode": "normal", "rebuild_reason": "", "habits": []})


# --------------------------------------------------------------------------- #
# 模块级默认实例 + 契约函数
# --------------------------------------------------------------------------- #
_store: PersonaStore = PersonaStore()


# ---- M1.4 人设 + 世界观 ----
def load_persona() -> dict[str, Any]:
    """读取当前人设卡（契约入口，见 PersonaStore.load_persona）。"""
    return _store.load_persona()


def update_persona(fields: dict[str, Any]) -> dict[str, Any]:
    return _store.update_persona(fields)


def rollback_persona(version: int) -> dict[str, Any]:
    return _store.rollback_persona(version)


def inject_lorebook(trigger: str) -> str:
    return _store.inject_lorebook(trigger)


# ---- M1.5 人物卡 ----
def add_kinship_card(
    name: str,
    relation: str,
    events: list[dict[str, Any]] | None = None,
    recent: str = "",
    *,
    memorial_anchor: bool = False,
    memorial_ask: bool = True,
    readonly: bool = False,
) -> KinshipCard:
    return _store.add_kinship_card(
        name, relation, events, recent,
        memorial_anchor=memorial_anchor, memorial_ask=memorial_ask, readonly=readonly,
    )


def update_kinship_card(name: str, **fields: Any) -> KinshipCard:
    return _store.update_kinship_card(name, **fields)


def get_kinship_card(name: str) -> KinshipCard | None:
    return _store.get_kinship_card(name)


def list_kinship_cards() -> list[KinshipCard]:
    return _store.list_kinship_cards()


def set_memorial_anchor(name: str, enabled: bool) -> KinshipCard:
    return _store.set_memorial_anchor(name, enabled)


def set_memorial_ask(name: str, enabled: bool) -> KinshipCard:
    return _store.set_memorial_ask(name, enabled)


# ---- M1.6 哀伤标签 ----
def add_grief_tag(entity: str) -> None:
    return _store.add_grief_tag(entity)


def grief_tags() -> list[dict[str, Any]]:
    return _store.grief_tags()


def grief_phase(entity: str, now: date | datetime | None = None) -> str:
    return _store.grief_phase(entity, now)


# ---- M1.7 惯例画像 ----
def habit_profile() -> dict[str, Any]:
    return _store.habit_profile()


def add_habit(content: str, category: str = "一般") -> dict[str, Any]:
    return _store.add_habit(content, category)


def enter_rebuild_mode(reason: str) -> dict[str, Any]:
    return _store.enter_rebuild_mode(reason)


def exit_rebuild_mode() -> dict[str, Any]:
    return _store.exit_rebuild_mode()


def clear_persona() -> None:
    """清空用户画像（契约入口，见 PersonaStore.clear）。"""
    return _store.clear()


__all__ = [
    "DEFAULT_PERSONA",
    "KinshipCard",
    "PersonaStore",
    "load_persona",
    "update_persona",
    "rollback_persona",
    "inject_lorebook",
    "add_kinship_card",
    "update_kinship_card",
    "get_kinship_card",
    "list_kinship_cards",
    "set_memorial_anchor",
    "set_memorial_ask",
    "add_grief_tag",
    "grief_tags",
    "grief_phase",
    "habit_profile",
    "add_habit",
    "enter_rebuild_mode",
    "exit_rebuild_mode",
    "clear_persona",
]
