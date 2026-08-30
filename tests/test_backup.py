"""scripts/backup.py 单元测试：备份生成+checksum、dest 隔离、保留 7 份、损坏检测。"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_TMP_BASE = Path(__file__).resolve().parent.parent / "logs"

from scripts import backup  # noqa: E402


class BackupTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _TMP_BASE / f"bk_{uuid.uuid4().hex[:8]}"
        self._tmp.mkdir(parents=True, exist_ok=True)
        self.tmp = self._tmp
        self.src = self.tmp / "src"
        self.dest = self.tmp / "dest"
        self.src.mkdir()
        (self.src / "memv4").mkdir()
        (self.src / "memv4" / "session_logs.json").write_text(
            json.dumps([{"id": "1", "content": "你好"}]),
            encoding="utf-8",
        )
        (self.src / "routes.jsonl").write_text("a\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_make_backup_creates_zip_and_checksum(self) -> None:
        archive = backup.make_backup(self.src, self.dest, keep=7)
        self.assertTrue(archive.exists())
        checksum = archive.with_suffix(".zip.sha256")
        self.assertTrue(checksum.exists())
        self.assertTrue(checksum.read_text(encoding="utf-8").strip())
        self.assertTrue(backup.verify_backup(archive))

    def test_backup_isolation_of_dest(self) -> None:
        # dest 位于 source 内时，不应把上次备份再打进新快照（避免递归膨胀）。
        inner_dest = self.src / "backup"
        archive = backup.make_backup(self.src, inner_dest, keep=7)
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
        self.assertTrue(names, "快照应包含源数据文件")
        self.assertFalse(any("backup" in n for n in names), "快照不应包含备份目录自身")

    def test_keep_rotation_limits_snapshots(self) -> None:
        # 伪造 9 份备份，cleanup keep=7 应只剩 7 份（默认 7 天策略）。
        self.dest.mkdir(parents=True, exist_ok=True)
        for i in range(9):
            ts = f"2026010{i + 1}_{i + 1:08d}"
            z = self.dest / f"xiao-data-backup_{ts}.zip"
            z.write_bytes(b"x")
            (z.with_suffix(".zip.sha256")).write_text("d\n", encoding="utf-8")
        backup._cleanup(self.dest, 7)
        snaps = list(self.dest.glob("xiao-data-backup_*.zip"))
        self.assertEqual(len(snaps), 7)

    def test_verify_detects_corruption(self) -> None:
        archive = backup.make_backup(self.src, self.dest, keep=7)
        data = bytearray(archive.read_bytes())
        data[0] ^= 0xFF
        archive.write_bytes(bytes(data))
        self.assertFalse(backup.verify_backup(archive))

    def test_cli_help_runs(self) -> None:
        # --help 应正常退出 (SystemExit 0)，不抛异常。
        import io
        from contextlib import redirect_stderr, redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            with self.assertRaises(SystemExit) as cm:
                backup.main(["--help"])
        self.assertEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()