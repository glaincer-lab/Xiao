"""长期记忆（v3）：跨会话记住用户明确要求记住的事，落盘 logs/memory.json。

- 只存显式记忆：用户说「记住…」时由 remember 工具写入，不做自动沉淀（避免噪音）
- 新会话注入：Agent 构建消息时把最近的记忆并入系统提示词（context_text）
- 一键清空：设置面板「清空记忆」→ /api/memory/clear → clear()
- 落盘格式：{"entries": [{id, text, ts, source}]}，原子写（tmp + replace），
  文件损坏时重置为空，不让坏文件卡死启动
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from backend.config import ROOT, config

DEFAULT_MAX_ENTRIES = 500
DEFAULT_INJECT_LIMIT = 30


class MemoryStore:
    def __init__(self, path: Path | str | None = None, max_entries: int | None = None) -> None:
        self._path = ROOT / "logs" / "memory.json" if path is None else Path(path)
        if max_entries is None:
            try:
                max_entries = int(config.get("memory.max_entries", DEFAULT_MAX_ENTRIES))
            except (TypeError, ValueError):
                max_entries = DEFAULT_MAX_ENTRIES
        self._max = max(1, max_entries)
        self._lock = threading.Lock()
        self._entries: list[dict] = self._load()

    def entries(self) -> list[dict]:
        """全部记忆（条目为拷贝，外部改动不影响内存态）。"""
        with self._lock:
            return [dict(e) for e in self._entries]

    def add(self, text: str, source: str = "user") -> dict:
        text = str(text or "").strip()
        if not text:
            raise ValueError("记忆内容不能为空")
        entry = {"id": uuid.uuid4().hex[:8], "text": text, "ts": time.time(), "source": source}
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._max:  # 超上限 FIFO：丢最旧的
                self._entries = self._entries[-self._max:]
        self._save()
        return dict(entry)

    def clear(self) -> None:
        with self._lock:
            self._entries = []
        self._save()

    def delete(self, entry_id: str) -> bool:
        """按 id 删除单条记忆；返回是否真的删除（id 不存在返回 False）。"""
        entry_id = str(entry_id or "").strip()
        if not entry_id:
            return False
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if str(e.get("id")) != entry_id]
            removed = before - len(self._entries)
        if removed:
            self._save()
        return removed > 0

    def delete_range(
        self,
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> int:
        """按时间区间删除（闭区间 [start_ts, end_ts]，按 entry 的 ts 过滤）。

        start_ts / end_ts 为 Unix 秒；缺省一端视为无界。返回删除条数。
        """
        lo = None if start_ts is None else float(start_ts)
        hi = None if end_ts is None else float(end_ts)
        with self._lock:
            before = len(self._entries)
            kept: list[dict] = []
            for e in self._entries:
                try:
                    ts = float(e.get("ts", 0) or 0)
                except (TypeError, ValueError):
                    ts = 0.0
                if lo is not None and ts < lo:
                    kept.append(e)
                    continue
                if hi is not None and ts > hi:
                    kept.append(e)
                    continue
                # 落在区间内 → 删除（不保留）
            self._entries = kept
            removed = before - len(self._entries)
        if removed:
            self._save()
        return removed

    def context_text(self, limit: int | None = None) -> str:
        """拼进系统提示词的注入文本；无记忆时返回空串（调用方据此跳过）。"""
        if limit is None:
            try:
                limit = int(config.get("memory.inject_limit", DEFAULT_INJECT_LIMIT))
            except (TypeError, ValueError):
                limit = DEFAULT_INJECT_LIMIT
        limit = max(1, limit)
        with self._lock:
            items = self._entries[-limit:]
        if not items:
            return ""
        lines = [
            f"{i}. [{time.strftime('%Y-%m-%d', time.localtime(e['ts']))}] {e['text']}"
            for i, e in enumerate(items, 1)
        ]
        return "以下是用户之前明确让你记住的事（跨会话长期记忆），相关时自然参考，不要逐条复述：\n" + "\n".join(lines)

    def _load(self) -> list[dict]:
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("entries") if isinstance(data, dict) else None
            if not isinstance(entries, list):
                return []
            return [e for e in entries if isinstance(e, dict) and str(e.get("text", "")).strip()]
        except FileNotFoundError:
            return []
        except Exception:  # noqa: BLE001
            print(f"[memory] 记忆文件损坏，已重置: {self._path}")
            return []

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump({"entries": self._entries}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError as e:
            print(f"[memory] 记忆保存失败: {e}")


memory_store = MemoryStore()
