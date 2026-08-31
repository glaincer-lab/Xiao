"""backend/router.py（路由层 · 日志脱敏 + 容量轮转）单元测试。

覆盖：①敏感词及其后续值被遮蔽为 [REDACTED]；②无敏感词时明文正常；③超过行数/字节上限轮转、旧天文件保留上限。
运行：python -m unittest tests.test_router -v
"""
from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from backend.router import Router, _sanitize

ROOT = Path(__file__).resolve().parent.parent
_TEST_DIR = ROOT / "logs" / "_test_router"


def _tmp_dir() -> Path:
    d = _TEST_DIR / uuid.uuid4().hex[:8]
    d.mkdir(parents=True, exist_ok=True)
    return d


class SanitizeTest(unittest.TestCase):
    def test_sensitive_redacted(self) -> None:
        out = _sanitize("我的密码是 abc123，请帮我记住")
        self.assertIn("[REDACTED]", out)
        self.assertNotIn("abc123", out)

    def test_plain_unchanged(self) -> None:
        text = "今天天气不错，帮我写代码"
        self.assertEqual(_sanitize(text), text)


class RouterLogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = _tmp_dir()
        self.r = Router()
        self.r._log_path = str(self.dir / "routes.jsonl")
        self.r._log_max_lines = 100000
        self.r._log_max_bytes = 1048576
        self.r._log_keep_days = 5

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _read_log(self) -> list[dict]:
        p = Path(self.r._log_path)
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _history_files(self) -> list[Path]:
        d = Path(self.r._log_path).parent
        stem = Path(self.r._log_path).stem  # "routes"
        return sorted(d.glob(stem + "-*.json"))

    def test_log_text_redacted(self) -> None:
        self.r._mode = "auto"
        self.r._keywords = ["写代码"]
        decision = self.r.route("我的密码是 abc123，帮我写代码")
        self.assertEqual(decision, "dsh")
        rows = self._read_log()
        self.assertEqual(len(rows), 1)
        self.assertIn("[REDACTED]", rows[0]["text"])
        self.assertNotIn("abc123", rows[0]["text"])

    def test_log_plain_unchanged(self) -> None:
        self.r._mode = "chat"
        text = "今天天气不错"
        self.assertEqual(self.r.route(text), "chat")
        rows = self._read_log()
        self.assertEqual(rows[0]["text"], text)

    def test_rotate_on_line_limit(self) -> None:
        self.r._log_max_lines = 3
        self.r._log_max_bytes = 1048576
        for _ in range(4):
            self.r.route("你好")
        self.assertEqual(len(self._read_log()), 1)  # 第 4 条落在新文件
        hist = self._history_files()
        self.assertEqual(len(hist), 1)
        hist_rows = [json.loads(line) for line in hist[0].read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(hist_rows), 3)

    def test_rotate_on_byte_limit(self) -> None:
        self.r._log_max_lines = 100000
        self.r._log_max_bytes = 120
        self.r.route("测" * 200)
        self.r.route("测" * 200)
        self.assertEqual(len(self._read_log()), 1)
        self.assertEqual(len(self._history_files()), 1)

    def test_keep_days_cap(self) -> None:
        self.r._log_max_lines = 1
        self.r._log_max_bytes = 1048576
        self.r._log_keep_days = 2
        for _ in range(5):
            self.r.route("你好")
        self.assertLessEqual(len(self._history_files()), 2)
        self.assertTrue(Path(self.r._log_path).exists())


if __name__ == "__main__":
    unittest.main()
