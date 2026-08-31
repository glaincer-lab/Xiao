"""M6-M0 记忆全量导出/迁移层（设计书 M6-growth.md §4.3）。

一键打包九类资产（记忆/画像/人设/人物卡/成长记录/世界观/哀伤标签/入册状态/
纪念锚点）为单个 JSON，新设备导入实现「搬家不失忆」；按 id 选择性删除实现
「忘掉那段」被遗忘权。导出内容属高密度隐私 → 返回 dict 自带 "提示加密": True，
提示落盘前先加密。

数据来源全部为各持久层公开只读接口（DataTrack.items / PersonaStore.load_persona
/ habit_profile / list_kinship_cards / lorebook_entries 目录读取 / grief_tags /
GrowthStore 三轨读取）+ 直接读状态文件（canonizer_state.json / memorial.json）；
导入与删除为「快照恢复 / 条目移除」：直接写回与各持久层一致的落盘布局文件
（memv4: {root}/{kind}.json；persona: {root}/persona/*.json、
{root}/persona/lorebook/*.json、{root}/persona/grief.json；
growth: {root}/growth.json、{root}/canonizer_state.json、{root}/memorial.json），
保证 id/ts/version 等字段保真，不经业务 API 逐条重建（那会重新生成 id 破坏
跨资产引用）。原子写（tmp + os.replace）。

用法：
    ex = MemoryExporter(store, persona_root=..., memv4_root=..., bus=bus)
    data = ex.export()   # {"version", "提示加密", "记忆", "画像", "人设", "人物卡", "成长记录", "世界观", "哀伤标签", "入册状态", "纪念锚点"}
    ex.import_data(data)               # 恢复到同一组 root
    ex.forget("session_logs", id)      # 选择性删除
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from backend.config import ROOT
from backend.m6.growth import GrowthStore
from backend.memv1.persona import PersonaStore
from backend.memv4 import DataTrack

# 导出格式版本（设计书 §8 开放问题 2：跨版本兼容策略未定，先以 version 字段留扩展位）
EXPORT_VERSION = "1.0"

# memv4 数据轨三层（写死，与 backend/memv4.DATA_TRACK_KINDS 一致）
MEMORY_KINDS: tuple[str, ...] = ("session_logs", "raw_frames_meta", "context_snapshots")
# GrowthStore 三轨（写死，与 growth.json 布局一致）
GROWTH_TRACKS: tuple[str, ...] = ("user_track", "agent_track", "shared_memories")
# forget() 可作用的资产/层集合（人物卡 kinship 无 id，按 name 唯一标识）
ASSET_KINDS: tuple[str, ...] = MEMORY_KINDS + GROWTH_TRACKS + ("habits", "kinship")


def _atomic_write_json(path: Path, data: Any) -> None:
    """原子写 JSON（与各持久层同模式：tmp + os.replace）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class MemoryExporter:
    """M6 §4.3 记忆全量导出/迁移：九类资产一键打包、导入恢复、选择性删除。

    Args:
        store: GrowthStore 实例（导出源 / 导入目标，root 从其 _root 获取）。
        persona_root: PersonaStore 根（缺省项目根 ROOT，文件在 root/persona/）。
        memv4_root: DataTrack 根（缺省 ROOT/logs/memv4，文件直接在 root/）。
        bus: 可选 EventBus；传入时 export() 发布 "memory.export_requested"。
    """

    def __init__(
        self,
        store: GrowthStore,
        *,
        persona_root: Path | str | None = None,
        memv4_root: Path | str | None = None,
        bus: Any | None = None,
    ) -> None:
        self._store = store
        self._growth_root = Path(getattr(store, "root", None) or ROOT / "logs" / "m6")
        self._persona_root = ROOT if persona_root is None else Path(persona_root)
        self._memv4_root = ROOT / "logs" / "memv4" if memv4_root is None else Path(memv4_root)
        self._bus = bus

    # ------------------------------------------------------------------ #
    # 导出：一键打包四类资产（+ 成长记录冷却态），返回可 JSON 序列化 dict
    # ------------------------------------------------------------------ #

    def export(self) -> dict[str, Any]:
        """打包九类资产为单个 dict（含 version 与 "提示加密": True）。

        发布 "memory.export_requested" 事件（bus 注入时），payload {"范围": "全量"}。
        """
        data: dict[str, Any] = {
            "version": EXPORT_VERSION,
            "exported_at": time.time(),
            "提示加密": True,  # 高密度隐私 → 提示加密后再落盘/传输
            "记忆": self._export_memory(),
            "画像": self._export_habits(),
            "人设": self._export_persona(),
            "人物卡": self._export_kinship(),
            "成长记录": self._export_growth(),
            "世界观": self._export_lorebook(),
            "哀伤标签": self._export_grief(),
            "入册状态": self._export_canonizer_state(),
            "纪念锚点": self._export_memorial(),
        }
        if self._bus is not None:
            self._bus.emit("memory.export_requested", {"范围": "全量"})
        return data

    def _export_memory(self) -> dict[str, Any]:
        track = DataTrack(root=self._memv4_root)
        return {k: track.items(k) for k in MEMORY_KINDS}

    def _export_habits(self) -> dict[str, Any]:
        return PersonaStore(root=self._persona_root).habit_profile()

    def _export_persona(self) -> dict[str, Any]:
        return PersonaStore(root=self._persona_root).load_persona()

    def _export_kinship(self) -> list[dict[str, Any]]:
        p = PersonaStore(root=self._persona_root)
        return [c.to_dict() for c in p.list_kinship_cards()]

    def _export_growth(self) -> dict[str, Any]:
        return {
            "user_track": self._store.user_records(),
            "agent_track": self._store.agent_records(),
            "shared_memories": self._store.shared_memories(),
            "micro_cooling": self._store.micro_cooling(),
        }

    def _export_lorebook(self) -> dict[str, list[Any]]:
        """打包世界观：persona/lorebook/*.json 逐文件保留「文件名→内容」映射。

        每个文件为 list[dict]（如 world.json / self.json）；空目录给 {}。
        """
        out: dict[str, list[Any]] = {}
        lorebook_dir = self._persona_root / "persona" / "lorebook"
        if not lorebook_dir.is_dir():
            return out
        for path in sorted(lorebook_dir.glob("*.json")):
            data = _load_json(path, [])
            out[path.name] = data if isinstance(data, list) else []
        return out

    def _export_grief(self) -> list[dict[str, Any]]:
        """打包哀伤标签：persona/grief.json（list，缺失给 []）。"""
        data = _load_json(self._persona_root / "persona" / "grief.json", [])
        return data if isinstance(data, list) else []

    def _export_canonizer_state(self) -> dict[str, Any]:
        """打包入册状态：canonizer_state.json（缺失给 {"pending": [], "cooldowns": {}, "weekly": {}}）。"""
        return _load_json(
            self._growth_root / "canonizer_state.json",
            {"pending": [], "cooldowns": {}, "weekly": {}},
        )

    def _export_memorial(self) -> dict[str, Any]:
        """打包纪念锚点：memorial.json（缺失给 {"last_ask": {}}）。"""
        return _load_json(self._growth_root / "memorial.json", {"last_ask": {}})

    # ------------------------------------------------------------------ #
    # 导入：从导出 dict 恢复到本实例持有的三个 root（快照写回，字段保真）
    # ------------------------------------------------------------------ #

    def import_data(self, data: dict[str, Any]) -> None:
        """把导出 dict 恢复到指定 root；缺段给默认空，格式无效给人类可读报错。"""
        if not isinstance(data, dict):
            raise ValueError("导入数据必须是 dict（M6 导出文件为 JSON 对象）")
        version = data.get("version")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("导入文件缺少有效的 version 字段，无法识别导出格式")

        # 记忆（memv4 数据轨三层，逐层校验为 list 后写回）
        mem = data.get("记忆")
        if not isinstance(mem, dict):
            mem = {}
        for kind in MEMORY_KINDS:
            rows = mem.get(kind, [])
            if not isinstance(rows, list):
                raise ValueError(f"记忆层 {kind} 必须是列表，收到 {type(rows).__name__}")
            _atomic_write_json(self._memv4_root / f"{kind}.json", rows)

        # 画像（惯例画像 habit.json：{mode, rebuild_reason, habits}）
        habits = data.get("画像")
        if isinstance(habits, dict):
            _atomic_write_json(self._persona_root / "persona" / "habit.json", habits)

        # 人设（persona.json：load_persona 返回结构）
        persona = data.get("人设")
        if isinstance(persona, dict):
            _atomic_write_json(self._persona_root / "persona" / "persona.json", persona)

        # 人物卡（kinship.json：list[to_dict]）
        cards = data.get("人物卡")
        if isinstance(cards, list):
            _atomic_write_json(self._persona_root / "persona" / "kinship.json", cards)

        # 成长记录（growth.json：三轨 + micro_requests 冷却态）
        growth = data.get("成长记录")
        if isinstance(growth, dict):
            out: dict[str, Any] = {
                "user_track": self._as_list(growth.get("user_track"), "user_track"),
                "agent_track": self._as_list(growth.get("agent_track"), "agent_track"),
                "shared_memories": self._as_list(growth.get("shared_memories"), "shared_memories"),
                "micro_requests": {},
            }
            mc = growth.get("micro_cooling")
            if isinstance(mc, dict) and (
                mc.get("cooldown_until") is not None or mc.get("last_type") is not None
            ):
                out["micro_requests"] = {
                    "cooldown_until": mc.get("cooldown_until"),
                    "last_type": mc.get("last_type"),
                }
            _atomic_write_json(self._growth_root / "growth.json", out)
            self._refresh_store()

        # 世界观（persona/lorebook/*.json：逐文件写回，保留「文件名→内容」映射）
        lorebook = data.get("世界观")
        if isinstance(lorebook, dict):
            for name, entries in lorebook.items():
                if not isinstance(entries, list):
                    raise ValueError(
                        f"世界观 {name!r} 必须是列表，收到 {type(entries).__name__}"
                    )
                if _unsafe_filename(name):
                    raise ValueError(f"世界观文件名非法：{name!r}")
                _atomic_write_json(self._persona_root / "persona" / "lorebook" / name, entries)

        # 哀伤标签（persona/grief.json：list）
        grief = data.get("哀伤标签")
        if isinstance(grief, list):
            _atomic_write_json(self._persona_root / "persona" / "grief.json", grief)

        # 入册状态（canonizer_state.json：{pending, cooldowns, weekly}）
        state = data.get("入册状态")
        if isinstance(state, dict):
            _atomic_write_json(self._growth_root / "canonizer_state.json", state)

        # 纪念锚点（memorial.json：{last_ask}）
        memorial = data.get("纪念锚点")
        if isinstance(memorial, dict):
            _atomic_write_json(self._growth_root / "memorial.json", memorial)

    @staticmethod
    def _as_list(value: Any, label: str) -> list[Any]:
        if not isinstance(value, list):
            raise ValueError(f"成长记录 {label} 必须是列表，收到 {type(value).__name__}")
        return value


    def _refresh_store(self) -> None:
        """导入/删除改动 growth.json 后，刷新传入 GrowthStore 的内存态（立即可见）。"""
        reload = getattr(self._store, "reload", None)
        if callable(reload):
            reload()

    # ------------------------------------------------------------------ #
    # 选择性删除（"忘掉那段"被遗忘权）：按 id 删指定条目，其余保留
    # ------------------------------------------------------------------ #

    def forget(self, asset: str, record_id: str) -> None:
        """删除指定资产/层中的一条记录。

        Args:
            asset: 记忆层（session_logs/raw_frames_meta/context_snapshots）、
                成长轨（user_track/agent_track/shared_memories）、画像习惯
                （habits，按 id）、人物卡（kinship，按 name）。
            record_id: 目标条目唯一标识（人物卡为 name）。

        Raises:
            ValueError: asset 未知、record_id 为空或目标不存在。
        """
        asset = str(asset or "").strip()
        record_id = str(record_id or "").strip()
        if not record_id:
            raise ValueError("请提供要删除的条目 id（人物卡为 name）")

        if asset in MEMORY_KINDS:
            path = self._memv4_root / f"{asset}.json"
            rows = _load_json(path, [])
            if not isinstance(rows, list):
                rows = []
            kept = [r for r in rows if isinstance(r, dict) and str(r.get("id", "")) != record_id]
            self._require_removed(kept, len(rows), f"记忆层 {asset}", record_id)
            _atomic_write_json(path, kept)
        elif asset in GROWTH_TRACKS:
            path = self._growth_root / "growth.json"
            data = _load_json(path, {})
            if not isinstance(data, dict):
                data = {}
            rows = data.get(asset, [])
            if not isinstance(rows, list):
                rows = []
            kept = [r for r in rows if isinstance(r, dict) and str(r.get("id", "")) != record_id]
            self._require_removed(kept, len(rows), f"成长记录 {asset}", record_id)
            data[asset] = kept
            _atomic_write_json(path, data)
            self._refresh_store()
        elif asset == "habits":
            path = self._persona_root / "persona" / "habit.json"
            data = _load_json(path, {"mode": "normal", "rebuild_reason": "", "habits": []})
            if not isinstance(data, dict):
                data = {"mode": "normal", "rebuild_reason": "", "habits": []}
            habits = data.get("habits", [])
            if not isinstance(habits, list):
                habits = []
            kept = [h for h in habits if isinstance(h, dict) and str(h.get("id", "")) != record_id]
            self._require_removed(kept, len(habits), "画像习惯", record_id)
            data["habits"] = kept
            _atomic_write_json(path, data)
        elif asset == "kinship":
            path = self._persona_root / "persona" / "kinship.json"
            cards = _load_json(path, [])
            if not isinstance(cards, list):
                cards = []
            kept = [c for c in cards if isinstance(c, dict) and str(c.get("name", "")) != record_id]
            self._require_removed(kept, len(cards), "人物卡", record_id)
            _atomic_write_json(path, kept)
        else:
            raise ValueError(
                f"未知资产/层：{asset!r}。支持 {sorted(ASSET_KINDS)}"
                "（记忆三层 / 成长三轨 / habits / kinship）"
            )

    @staticmethod
    def _require_removed(kept: list[Any], before: int, label: str, record_id: str) -> None:
        if len(kept) == before:
            raise ValueError(f"{label} 中找不到 id={record_id!r} 的条目，未做任何删除")


def _unsafe_filename(name: str) -> bool:
    """世界观文件名必须是纯文件名（防目录穿越写入 persona 之外）。"""
    return (
        not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
    )


__all__ = ["MemoryExporter", "EXPORT_VERSION"]
