"""多任务并发自测：web 桥真并发、headless 钳制串行、按任务取消与语音消歧义。

TaskManager 经 mock.patch backend.tasks.config 注入临时日志路径，
桥用极简 fake（supports_concurrent 标志 + run/cancel 计数）驱动，不依赖真实 DSH。
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from backend.core import Pipeline
from backend.tasks import TaskManager


class _ConcBridge:
    supports_concurrent = True

    def __init__(self) -> None:
        self.running = 0
        self.peak = 0
        self.cancel_calls = 0

    async def run(self, text, grant=None):
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await asyncio.sleep(0.3)
            return f"ok:{text}"
        finally:
            self.running -= 1

    def cancel(self) -> None:
        self.cancel_calls += 1


class _SerialBridge:
    supports_concurrent = False

    def __init__(self) -> None:
        self.running = 0
        self.peak = 0
        self.cancel_calls = 0
        self._cancel_requested = False

    async def run(self, text, grant=None):
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            for _ in range(30):
                if self._cancel_requested:
                    raise RuntimeError("已取消")
                await asyncio.sleep(0.01)
            return f"ok:{text}"
        finally:
            self.running -= 1

    def cancel(self) -> None:
        self.cancel_calls += 1
        self._cancel_requested = True


def _make_tm(bridge) -> TaskManager:
    tmp = tempfile.mkdtemp()

    def _get(key, default=None):
        return {
            "tasks.max_concurrent": 2,
            "tasks.log_path": os.path.join(tmp, "tasks.json"),
        }.get(key, default)

    with mock.patch("backend.tasks.config") as cfg:
        cfg.get.side_effect = _get
        return TaskManager(bridge)


class TaskConcurrencyTests(unittest.TestCase):
    def test_web_bridge_runs_concurrently(self) -> None:
        bridge = _ConcBridge()
        tm = _make_tm(bridge)

        async def scene() -> None:
            tm.submit("任务A")
            tm.submit("任务B")
            await asyncio.sleep(0.1)
            self.assertEqual(bridge.peak, 2)
            await asyncio.sleep(0.4)

        asyncio.run(scene())
        self.assertEqual([t["status"] for t in tm.list()], ["done", "done"])

    def test_headless_bridge_clamps_to_serial(self) -> None:
        bridge = _SerialBridge()
        tm = _make_tm(bridge)
        self.assertEqual(tm._limit, 1)

        async def scene() -> None:
            tm.submit("任务一")
            tm.submit("任务二")
            await asyncio.sleep(0.15)
            self.assertEqual(bridge.peak, 1)
            for _ in range(300):
                if all(t["status"] == "done" for t in tm.list()):
                    break
                await asyncio.sleep(0.01)

        asyncio.run(scene())
        self.assertEqual([t["status"] for t in tm.list()], ["done", "done"])

    def test_web_cancel_kills_only_target(self) -> None:
        bridge = _ConcBridge()
        tm = _make_tm(bridge)
        ids: list[str] = []

        async def scene() -> None:
            ids.append(tm.submit("任务A"))
            ids.append(tm.submit("任务B"))
            await asyncio.sleep(0.05)
            self.assertTrue(tm.cancel(ids[0]))
            await asyncio.sleep(0.4)

        asyncio.run(scene())
        id_a, id_b = ids
        by_id = {t["id"]: t for t in tm.list()}
        self.assertEqual(by_id[id_a]["status"], "cancelled")
        self.assertEqual(by_id[id_b]["status"], "done")
        self.assertEqual(bridge.cancel_calls, 0)

    def test_pending_cancel_never_runs(self) -> None:
        bridge = _ConcBridge()
        tm = _make_tm(bridge)

        async def scene() -> None:
            tm.submit("任务A")
            tm.submit("任务B")
            id_c = tm.submit("任务C")
            await asyncio.sleep(0.02)
            self.assertEqual(tm._tasks[id_c]["status"], "pending")
            self.assertTrue(tm.cancel(id_c))
            await asyncio.sleep(0.4)

        asyncio.run(scene())
        by_id = {t["id"]: t for t in tm.list()}
        statuses = sorted(t["status"] for t in by_id.values())
        self.assertEqual(statuses.count("cancelled"), 1)
        self.assertEqual(statuses.count("done"), 2)

    def test_default_cancel_targets_latest(self) -> None:
        bridge = _ConcBridge()
        tm = _make_tm(bridge)
        ids: list[str] = []

        async def scene() -> None:
            ids.append(tm.submit("任务A"))
            ids.append(tm.submit("任务B"))
            await asyncio.sleep(0.05)
            self.assertTrue(tm.cancel())
            await asyncio.sleep(0.4)

        asyncio.run(scene())
        id_a, id_b = ids
        by_id = {t["id"]: t for t in tm.list()}
        self.assertEqual(by_id[id_a]["status"], "done")
        self.assertEqual(by_id[id_b]["status"], "cancelled")

    def test_headless_cancel_uses_bridge_cancel(self) -> None:
        bridge = _SerialBridge()
        tm = _make_tm(bridge)
        ids: list[str] = []

        async def scene() -> None:
            ids.append(tm.submit("任务A"))
            await asyncio.sleep(0.02)
            self.assertTrue(tm.cancel(ids[0]))
            await asyncio.sleep(0.3)

        asyncio.run(scene())
        (id_a,) = ids
        by_id = {t["id"]: t for t in tm.list()}
        self.assertEqual(by_id[id_a]["status"], "cancelled")
        self.assertEqual(bridge.cancel_calls, 1)
        self.assertEqual(bridge.peak, 1)


class VoiceCancelTests(unittest.TestCase):
    def _pipeline_with(self, tasks) -> Pipeline:
        p = Pipeline.__new__(Pipeline)
        p._tasks = tasks
        return p

    def test_cancel_by_ordinal(self) -> None:
        tm = mock.Mock()
        tm.active.return_value = [
            {"id": "a", "text": "任务一", "status": "running"},
            {"id": "b", "text": "任务二", "status": "running"},
        ]
        p = self._pipeline_with(tm)
        target, note = p._match_task_target("取消第2个任务")
        self.assertEqual(target["id"], "b")
        self.assertIsNone(note)

    def test_cancel_by_quoted_name(self) -> None:
        tm = mock.Mock()
        tm.active.return_value = [{"id": "a", "text": "下载这个大文件", "status": "running"}]
        p = self._pipeline_with(tm)
        target, note = p._match_task_target("取消「下载这个大文件」")
        self.assertEqual(target["id"], "a")
        self.assertIsNone(note)
        target2, note2 = p._match_task_target("取消「不存在的任务」")
        self.assertIsNone(target2)
        self.assertEqual(note2, "没有找到这个任务。")

    def test_cancel_ordinal_out_of_range(self) -> None:
        tm = mock.Mock()
        tm.active.return_value = [{"id": "a", "text": "任务一", "status": "running"}]
        p = self._pipeline_with(tm)
        target, note = p._match_task_target("取消第3个")
        self.assertIsNone(target)
        self.assertEqual(note, "现在只有 1 个任务在进行。")

    def test_bare_cancel_targets_most_recent(self) -> None:
        tm = mock.Mock()
        tm.active.return_value = [{"id": "a", "text": "任务A", "status": "running"}]
        p = self._pipeline_with(tm)
        target, note = p._match_task_target("算了，取消吧")
        self.assertIsNone(target)
        self.assertIsNone(note)

    def test_parse_ordinal(self) -> None:
        p = self._pipeline_with(mock.Mock())
        self.assertEqual(p._parse_ordinal("取消第3个"), 3)
        self.assertEqual(p._parse_ordinal("取消第三个"), 3)
        self.assertEqual(p._parse_ordinal("取消第 12 个"), 12)
        self.assertEqual(p._parse_ordinal("取消第十个"), 10)
        self.assertEqual(p._parse_ordinal("取消第二十一个"), 21)
        self.assertEqual(p._parse_ordinal("取消第两个"), 2)
        self.assertIsNone(p._parse_ordinal("取消吧"))


if __name__ == "__main__":
    unittest.main()
