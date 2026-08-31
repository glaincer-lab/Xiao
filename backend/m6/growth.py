"""M6-M0 成长记录双轨数据层（接口契约冻结 v1.0）。

GrowthStore 是 M6 各业务包（canonize / micro_request / export / recall）的唯一持久层：
- user_track / agent_track 双轨分离（呈现不混合）；
- shared_memories 共同记忆；
- micro_requests 微小请求月级冷却（cooldown_until + last_type）。

数据落 `ROOT/logs/m6/growth.json`（root 可注入隔离测试），原子写（tmp + os.replace）、线程安全。
本层只落盘/读取，**不发布事件**——事件发布在业务层（canonize 发 growth.canonized 等）。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

from backend.config import ROOT

# 微小请求三类（设计书 M6 §4.2）：求反馈 / 求确认偏好 / 求人类经验
MICRO_TYPES = ("feedback", "preference", "human_experience")


class GrowthStore:
    """成长记录双轨持久层（append-only 数据，删除仅限「选择性删除」走 export 层）。"""

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = ROOT / "logs" / "m6" if root is None else Path(root)
        self._path = self._root / "growth.json"
        self._lock = threading.Lock()
        self._data = self._load()

    @property
    def root(self) -> Path:
        """落盘根目录（只读，供业务层推导同目录状态文件路径，如 canonizer_state.json）。"""
        return self._root

    # ---- 双轨追加 ----

    def add_user_record(
        self,
        milestone: str,
        *,
        source: str = "explicit",
        canon: bool = False,
        date: str | None = None,
    ) -> dict[str, Any]:
        """用户轨追加一条成长记录（canon=True 仅在用户册封后由 canonize 层传入）。"""
        record = {
            "id": self._new_id(),
            "ts": time.time(),
            "date": date or self._today(),
            "milestone": str(milestone),
            "source": str(source),
            "canon": bool(canon),
        }
        self._append("user_track", record)
        return record

    def add_agent_record(
        self,
        milestone: str,
        *,
        capability_event: str,
        canon: bool = False,
        date: str | None = None,
    ) -> dict[str, Any]:
        """小二轨追加一条成长记录；capability_event 必填（可追溯到真实系统能力事件，不可编造）。"""
        if not str(capability_event or "").strip():
            raise ValueError("agent_track 的 capability_event 不能为空（须可追溯到真实系统能力事件）")
        record = {
            "id": self._new_id(),
            "ts": time.time(),
            "date": date or self._today(),
            "milestone": str(milestone),
            "capability_event": str(capability_event),
            "canon": bool(canon),
        }
        self._append("agent_track", record)
        return record

    def add_shared_memory(self, event: str, *, luminance: int = 5, date: str | None = None) -> dict[str, Any]:
        """共同记忆追加一条（luminance 情感高光度 0-5，与 M1 affective_luminance 同刻度）。"""
        luminance = max(0, min(5, int(luminance)))
        record = {
            "id": self._new_id(),
            "ts": time.time(),
            "date": date or self._today(),
            "event": str(event),
            "luminance": luminance,
        }
        self._append("shared_memories", record)
        return record

    # ---- 读取（双轨分离，供分栏呈现） ----

    def user_records(self) -> list[dict[str, Any]]:
        """用户轨全部记录（拷贝，时间倒序）。"""
        return self._records("user_track")

    def agent_records(self) -> list[dict[str, Any]]:
        """小二轨全部记录（拷贝，时间倒序）。"""
        return self._records("agent_track")

    def shared_memories(self) -> list[dict[str, Any]]:
        """共同记忆全部记录（拷贝，时间倒序）。"""
        return self._records("shared_memories")

    # ---- 微小请求冷却 ----

    def micro_cooling(self) -> dict[str, Any]:
        """返回 {cooldown_until: float|None, last_type: str|None}。"""
        with self._lock:
            mc = self._data.get("micro_requests") or {}
            return {
                "cooldown_until": mc.get("cooldown_until"),
                "last_type": mc.get("last_type"),
            }

    def set_micro_cooling(self, last_type: str, cooldown_until: float) -> None:
        """记录微小请求类型与下次可问时间戳（月级冷却）。"""
        if last_type not in MICRO_TYPES:
            raise ValueError(f"微小请求类型必须是 {sorted(MICRO_TYPES)} 之一，收到 {last_type!r}")
        with self._lock:
            self._data["micro_requests"] = {
                "cooldown_until": float(cooldown_until),
                "last_type": str(last_type),
            }
            self._save()

    def reload(self) -> None:
        """从磁盘重载内存态（供业务层直接写回落盘文件后刷新，保持立即可见）。"""
        with self._lock:
            self._data = self._load()

    # ---- 内部 ----

    @staticmethod
    def _today() -> str:
        return date.today().isoformat()

    @staticmethod
    def _new_id() -> str:
        return os.urandom(6).hex()

    def _records(self, key: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(r) for r in self._data.get(key, [])]
        return sorted(rows, key=lambda r: r.get("ts", 0), reverse=True)

    def _append(self, key: str, record: dict[str, Any]) -> None:
        with self._lock:
            self._data.setdefault(key, []).append(record)
            self._save()

    def _load(self) -> dict[str, Any]:
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key in ("user_track", "agent_track", "shared_memories"):
                    if not isinstance(data.get(key), list):
                        data[key] = []
                if not isinstance(data.get("micro_requests"), dict):
                    data["micro_requests"] = {}
                return data
            return self._empty()
        except FileNotFoundError:
            return self._empty()
        except Exception:  # noqa: BLE001
            print(f"[m6] 成长记录文件损坏，已重置为空: {self._path}")
            return self._empty()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "user_track": [],
            "agent_track": [],
            "shared_memories": [],
            "micro_requests": {},
        }

    def _save(self) -> None:
        """原子写：先写临时文件，再 os.replace 覆盖目标（与 memv4 DataTrack 同模式）。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError as e:
            print(f"[m6] 成长记录保存失败: {e}")


__all__ = ["GrowthStore", "MICRO_TYPES"]
