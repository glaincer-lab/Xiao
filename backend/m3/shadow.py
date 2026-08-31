"""M3-M6 影子期假投递：只记录"会投什么"，不真投（默认态）。"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger("m3.shadow")


class ShadowRecorder:
    """影子期记录器：记录候选判定结果到内存 + 可选 jsonl；采集用户实际响应率。"""

    def __init__(self, log_path: str | Path | None = None, now_fn=None) -> None:
        self._log_path = Path(log_path) if log_path else None
        self._now_fn = now_fn or time.time
        self.records: list[dict] = []
        self._deliveries: list[float] = []
        self._responses: list[float] = []

    def record(self, entry: Mapping[str, Any]) -> None:
        """记录一条影子投递（notify shadow 模式回调）。"""
        rec = dict(entry)
        ts = self._now_fn()
        rec.setdefault("时间戳", ts)
        self.records.append(rec)
        self._deliveries.append(ts)
        self._append_jsonl(rec)

    def note_response(self, now: float | None = None) -> None:
        """记录用户实际响应（供灰度调参响应率）。"""
        self._responses.append(now if now is not None else self._now_fn())

    def response_rate(self) -> float:
        if not self._deliveries:
            return 0.0
        return round(len(self._responses) / len(self._deliveries), 3)

    def _append_jsonl(self, rec: dict) -> None:
        if self._log_path is None:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except Exception as ex:  # noqa: BLE001
            logger.warning("[m3.shadow] 影子日志写入失败: %s", ex)


__all__ = ["ShadowRecorder"]
