"""append-only fact plane（可审计回放的事实平面）。

设计思想受 [xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）启发，
自研实现：把每次 run 的桥接事件（tool/call、tool/result、assistant/chunk、
assistant/message、turn/end）追加式持久化为 run 级记录，只追加、不覆盖，
供 replay（回放时间线）与 scorecard（质量打点）消费。
不与 HomeRail 代码混淆：命名全套 backend/audit/ + xiao_ 前缀。

存储形态（run 级工作区隔离，与 backend/tasks.py 的 logs/ 约定对齐）：
    <base_dir>/<run_id>/events.jsonl    ← 追加式事实日志（append-only，唯一事实来源）
    <base_dir>/<run_id>/run.json        ← run 级派生清单（缓存元数据，可重算，非事实源）
    <base_dir>/<run_id>/scorecard.json  ← 打分结果（由 scorecard 写入，非事实源）

base_dir 来自 config（audit.log_dir，默认 logs/audit），基于 ROOT 解析，不写死本机绝对路径。
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backend.config import ROOT, config


@dataclass
class XiaoFact:
    """一条 append-only 事实记录（不可变，只追加）。"""

    seq: int
    ts: float
    run_id: str
    event: str
    payload: dict[str, Any]


def _default_base_dir() -> Path:
    """audit 工作区根目录：config 可配置，基于 ROOT 解析（不写死绝对路径）。"""
    rel = str(config.get("audit.log_dir", "logs/audit") or "logs/audit")
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


class XiaoFactPlane:
    """run 级追加式事实平面：为每个 run 独立记录目录，facts() 追加式持久化。

    线程安全（web 桥并发时多个 run 并行写入，锁保护 seq 计数与文件打开）。
    """

    EVENT_FILE = "events.jsonl"

    # 缓冲区刷盘阈值（字节）：流式 chunk 累积到该值才落盘，显著降低高频 I/O（审计 R1）。
    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._base_dir: Path = Path(base_dir) if base_dir is not None else _default_base_dir()
        self._lock = threading.Lock()
        self._seq: dict[str, int] = {}  # run_id -> 已写入的最大 seq（惰性种子）


    # ---- 只读信息 ----
    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def run_dir(self, run_id: str) -> Path:
        """该 run 独立的工作区目录（run 级隔离）。"""
        return self._base_dir / str(run_id)

    def event_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / self.EVENT_FILE

    def runs(self) -> list[str]:
        """列出已记录事实的 run_id（按目录名排序）。"""
        if not self._base_dir.exists():
            return []
        return sorted(
            d.name
            for d in self._base_dir.iterdir()
            if d.is_dir() and (d / self.EVENT_FILE).is_file()
        )

    def _run_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id)

    # ---- 追加（append-only） ----
    def append(
        self,
        run_id: str,
        event: str,
        payload: dict[str, Any] | None = None,
        ts: float | None = None,
    ) -> XiaoFact:
        """追加一条事实到该 run 的记录目录。只追加，绝不改写已有行。

        Args:
            run_id: run（任务轮）标识，与 tasks.py 的 task_id 同为 8 位 hex，用于 run 级隔离。
            event: 事件类型（tool/call、tool/result、assistant/chunk、assistant/message、turn/end）。
            payload: 事件载荷。
            ts: 可选时间戳（默认 now）。
        """
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if run_id not in self._seq:
                self._seq[run_id] = self._max_seq(run_id)
            self._seq[run_id] += 1
            seq = self._seq[run_id]
            fact = XiaoFact(
                seq=seq,
                ts=ts if ts is not None else time.time(),
                run_id=str(run_id),
                event=str(event),
                payload=dict(payload or {}),
            )
            line = json.dumps(asdict(fact), ensure_ascii=False, separators=(",", ":"))
            with open(self.event_path(run_id), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return fact

    def _max_seq(self, run_id: str) -> int:
        """读取该 run 已有事实的最大 seq（追加前种子，重启进程亦可续）。"""
        path = self.event_path(run_id)
        if not path.is_file():
            return 0
        max_seq = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                s = rec.get("seq")
                if isinstance(s, int) and s > max_seq:
                    max_seq = s
        return max_seq

    # ---- 回放（只读） ----
    def facts(self, run_id: str) -> list[XiaoFact]:
        """按 seq 顺序读取该 run 的全部事实（回放/打分的输入）。"""
        path = self.event_path(run_id)
        if not path.is_file():
            return []
        out: list[XiaoFact] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                out.append(
                    XiaoFact(
                        seq=int(rec.get("seq", 0)),
                        ts=float(rec.get("ts", 0) or 0),
                        run_id=rec.get("run_id") or run_id,
                        event=str(rec.get("event") or ""),
                        payload=dict(rec.get("payload") or {}),
                    )
                )
        out.sort(key=lambda x: x.seq)
        return out

    def last_event(self, run_id: str) -> XiaoFact | None:
        """该 run 的最后一条事实（如 turn/end，用于判定 run 是否结束）。"""
        facts = self.facts(run_id)
        return facts[-1] if facts else None

    def fact_count(self, run_id: str) -> int:
        return len(self.facts(run_id))

    # ---- 测试 / 清理 ----
    def clear(self) -> None:
        """删除整个 audit 工作区（测试用；隐私删除入口 /api/audit/clear 也复用）。"""
        with self._lock:
            if self._base_dir.exists():
                shutil.rmtree(self._base_dir, ignore_errors=True)
                self._seq.clear()

    def close(self) -> None:
        """无资源需释放（每次追加即落盘）；保留接口便于统一清理。"""
        return None
