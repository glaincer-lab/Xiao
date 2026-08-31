"""语音操电脑会话级放行缓存（computer.py _confirm）单元测试。

覆盖：批准后 TTL 内同类免重复询问、拒绝不缓存、不同类别独立、reset 清空、TTL 过期重新问。
"""
from __future__ import annotations

import asyncio
import unittest

from backend.tools import computer


def _run(coro):
    return asyncio.run(coro)


class ApprovalCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        computer.reset_approval_cache()

    def tearDown(self) -> None:
        computer.reset_approval_cache()
        computer.set_confirm_hook(None)

    def test_first_approval_asks_then_caches(self) -> None:
        calls: list[str] = []

        async def hook(q: str) -> bool:
            calls.append(q)
            return True

        computer.set_confirm_hook(hook)
        self.assertIsNone(_run(computer._confirm("type", "允许打字？")))
        self.assertIsNone(_run(computer._confirm("type", "允许打字？")))
        self.assertEqual(len(calls), 1)  # 第二次 TTL 内免询问

    def test_reject_does_not_cache(self) -> None:
        calls: list[str] = []

        async def hook(q: str) -> bool:
            calls.append(q)
            return False

        computer.set_confirm_hook(hook)
        self.assertIsNotNone(_run(computer._confirm("type", "x")))
        self.assertIsNotNone(_run(computer._confirm("type", "x")))
        self.assertEqual(len(calls), 2)  # 拒绝不缓存，每次都问

    def test_reset_clears_cache(self) -> None:
        calls: list[str] = []

        async def hook(q: str) -> bool:
            calls.append(q)
            return True

        computer.set_confirm_hook(hook)
        _run(computer._confirm("type", "x"))
        computer.reset_approval_cache()
        _run(computer._confirm("type", "x"))
        self.assertEqual(len(calls), 2)

    def test_categories_independent(self) -> None:
        calls: list[str] = []

        async def hook(q: str) -> bool:
            calls.append(q)
            return True

        computer.set_confirm_hook(hook)
        _run(computer._confirm("type", "x"))
        _run(computer._confirm("mouse", "x"))  # 不同类别仍需询问
        self.assertEqual(len(calls), 2)

    def test_expired_ttl_reasks(self) -> None:
        calls: list[str] = []

        async def hook(q: str) -> bool:
            calls.append(q)
            return True

        computer.set_confirm_hook(hook)
        _run(computer._confirm("type", "x"))
        computer._approval_cache["type"] = 0.0  # 模拟 TTL 过期
        _run(computer._confirm("type", "x"))
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
