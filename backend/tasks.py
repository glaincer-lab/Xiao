"""长任务后台化：任务注册表 + 后台执行队列 + 状态/事件/持久化。

设计：
- 每个任务一个 asyncio 后台协程，经 Semaphore 限流（默认 1，即串行执行 DSH，保证稳定；
  可通过 config 的 tasks.max_concurrent 调大，但并发 DSH 子进程会竞争 headless profile，慎调）。
- 状态：pending（排队）→ running（执行）→ done / failed / cancelled。
- 状态变化通过 emit('task_event', ...) 推给前端；完成/失败时回调 notify 做语音播报。
- 历史持久化到 logs/tasks.json，重启后仍能看到上次任务列表。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

from backend.config import ROOT, config
from backend.session.state import emit


class TaskManager:
    def __init__(self, bridge) -> None:
        self._bridge = bridge
        self._tasks: dict[str, dict] = {}
        self._order: list[str] = []
        # 固定并发 1：DSHBridge 为单进程槽（_proc/_cancelled），并发任务会互相取消
        self._limit = 1
        self._sem = asyncio.Semaphore(self._limit)
        self._log_path = os.path.join(ROOT, str(config.get("tasks.log_path", "logs/tasks.json")))
        self._load()

    # ---- 提交 ----
    def submit(self, text: str, grant=None, notify=None) -> str:
        task_id = uuid.uuid4().hex[:8]
        task = {
            "id": task_id,
            "text": text,
            "status": "pending",
            "grant": sorted(grant or []),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }
        self._tasks[task_id] = task
        self._order.append(task_id)
        self._save()
        self._emit_task(task)
        asyncio.get_running_loop().create_task(self._run(task_id, notify))
        return task_id

    # ---- 后台执行 ----
    async def _run(self, task_id: str, notify) -> None:
        task = self._tasks[task_id]
        async with self._sem:
            if task["status"] != "pending":
                return  # 排队期间已被取消
            task["status"] = "running"
            task["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._save()
            self._emit_task(task)
            try:
                result = await self._bridge.run(task["text"], grant=set(task["grant"]))
                task["status"] = "done"
                task["result"] = result
            except Exception as e:  # noqa: BLE001
                # 若 cancel() 已把状态置为 cancelled（bridge.run 因取消而抛异常），不覆盖
                if task.get("status") != "cancelled":
                    task["status"] = "failed"
                    task["error"] = str(e)
            task["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._save()
        self._emit_task(task)
        if notify is not None:
            try:
                await notify(task)
            except Exception:  # noqa: BLE001
                pass

    # ---- 取消 / 查询 ----
    def cancel(self, task_id: str | None = None) -> bool:
        """取消指定任务；不传 id 时取消最近一个未完成的任务。"""
        if task_id is None:
            for tid in reversed(self._order):
                if self._tasks[tid]["status"] in ("pending", "running"):
                    task_id = tid
                    break
        if task_id is None:
            return False
        task = self._tasks.get(task_id)
        if task is None or task["status"] not in ("pending", "running"):
            return False
        if task["status"] == "running":
            self._bridge.cancel()
        task["status"] = "cancelled"
        task["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._save()
        self._emit_task(task)
        return True

    def active(self) -> list[dict]:
        return [self._tasks[i] for i in self._order if self._tasks[i]["status"] in ("pending", "running")]

    def list(self, limit: int = 30) -> list[dict]:
        return [self._tasks[i] for i in self._order[-limit:]]

    # ---- 事件 / 持久化 ----
    def _emit_task(self, task: dict) -> None:
        emit("task_event", **task)

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
            with open(self._log_path, "w", encoding="utf-8") as f:
                json.dump([self._tasks[i] for i in self._order], f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load(self) -> None:
        try:
            if os.path.isfile(self._log_path):
                with open(self._log_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for t in data:
                        if isinstance(t, dict) and t.get("id"):
                            self._tasks[t["id"]] = t
                            self._order.append(t["id"])
        except Exception:
            pass
