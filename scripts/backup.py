"""Xiao 数据备份脚本（T4·P1 止血）：每日快照 logs/ 下数据目录，附 SHA-256 校验。

背景：
    本机无 SQLite，业务数据以 JSON 存于 logs/（memv4 数据轨 logs/memv4/*.json、
    logs/routes.jsonl、未来可能新增的画像/事件数据）。本脚本把 logs/ 下的数据
    快照备份到异目录（默认 logs/backup/），保留最近 N 份（默认 7，即 7 天），
    每份备份附 SHA-256 校验文件；校验失败时提示从最近一份有效备份恢复。

用法（第三使用者视角，路径全部基于 Path(__file__) 相对项目根，不写死本机绝对路径）：

    python scripts/backup.py                       # 备份 logs/ -> logs/backup/，保留 7 份
    python scripts/backup.py --keep 14             # 保留最近 14 份
    python scripts/backup.py --source logs/memv4 --dest logs/backup
    python scripts/backup.py --verify              # 校验最近一份备份的 checksum
    python scripts/backup.py --verify-file <zip>   # 校验指定备份的 checksum

仅标准库，依赖零新增；MIT。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# 项目根：由本脚本位置相对定位（scripts/backup.py -> 项目根）。
ROOT = Path(__file__).resolve().parent.parent

# 默认源/目的目录（均在项目内，可被 --source/--dest 覆盖）。
DEFAULT_SOURCE = ROOT / "logs"
DEFAULT_DEST = ROOT / "logs" / "backup"

# 备份文件命名前缀（用于识别与旧份清理）。
_BACKUP_PREFIX = "xiao-data-backup_"
_KEEP_DEFAULT = 7


def _sha256_of_file(path: Path) -> str:
    """计算文件 SHA-256（分块读，避免大文件占内存）。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _source_files(source: Path, skip: Path | None = None):
    """遍历 source 下全部文件，跳过 skip（备份目录）自身及其子树。"""
    for p in sorted(source.rglob("*")):
        if not p.is_file():
            continue
        if skip is not None and (p == skip or skip in p.parents):
            continue
        yield p


def make_backup(source: Path, dest: Path, keep: int = _KEEP_DEFAULT) -> Path:
    """把 source 目录快照为 dest 下一个带时间戳的 zip，附 SHA-256 校验文件。

    Args:
        source: 要备份的数据目录。
        dest: 备份输出目录（若在 source 内会被自动跳过，避免递归备份）。
        keep: 保留最近备份份数（超出的旧份连同校验文件一并删除）。

    Returns:
        本次生成的备份 zip 路径。
    """
    source = source.resolve()
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = dest / f"{_BACKUP_PREFIX}{ts}.zip"

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        found = 0
        for f in _source_files(source, skip=dest):
            arcname = Path(source.name) / f.relative_to(source)
            zf.write(f, arcname)
            found += 1

    checksum_file = archive.with_suffix(".zip.sha256")
    digest = _sha256_of_file(archive)
    checksum_file.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")

    _cleanup(dest, keep)

    if found == 0:
        print(f"[backup] 源目录 {source} 下无可备份文件（仅生成空快照）。")
    print(f"[backup] 完成备份: {archive}（含 {found} 个文件）")
    print(f"[backup] 校验文件: {checksum_file}")
    return archive


def _cleanup(dest: Path, keep: int) -> None:
    """保留最近 keep 份备份，删除更旧的 zip 与其校验文件。"""
    keep = max(1, int(keep))
    snaps = sorted(
        dest.glob(f"{_BACKUP_PREFIX}*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in snaps[keep:]:
        sha = old.with_suffix(".zip.sha256")
        old.unlink(missing_ok=True)
        sha.unlink(missing_ok=True)
        print(f"[backup] 清理旧备份: {old.name}")
    print(f"[backup] 当前保留 {min(len(snaps), keep)} 份备份（上限 {keep} 份）。")


def verify_backup(archive: Path, checksum_path: Path | None = None) -> bool:
    """校验一份备份的 SHA-256；不匹配时提示从最近一份有效备份恢复。

    Returns:
        True 校验通过；False 校验失败或文件缺失。
    """
    archive = Path(archive)
    checksum_path = Path(checksum_path) if checksum_path else archive.with_suffix(".zip.sha256")
    if not archive.exists() or not checksum_path.exists():
        print(f"[backup] 备份或校验文件缺失: {archive}")
        return False

    stored = checksum_path.read_text(encoding="utf-8").strip().split()
    expected = stored[0] if stored else ""
    actual = _sha256_of_file(archive)

    if expected and expected == actual:
        print(f"[backup] 校验通过: {archive.name}")
        return True

    print(
        f"[backup] 校验失败: {archive.name} —— 备份已损坏，"
        "请从最近一份有效备份恢复（用 --verify 逐份体检定位最近可用者）。"
    )
    return False


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。返回退出码（0 成功；1 失败）。"""
    parser = argparse.ArgumentParser(
        description="Xiao 数据备份：快照 logs/ 数据目录到 logs/backup/，保留最近 N 份，附 SHA-256 校验。",
        epilog="路径默认基于项目根（Path(__file__) 相对定位），可用 --source/--dest 覆盖。",
    )
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="要备份的数据目录（默认 logs/）")
    parser.add_argument("--dest", default=str(DEFAULT_DEST), help="备份输出目录（默认 logs/backup/）")
    parser.add_argument("--keep", type=int, default=_KEEP_DEFAULT, help="保留最近备份份数（默认 7）")
    parser.add_argument("--verify", action="store_true", help="校验最近一份备份的 checksum")
    parser.add_argument("--verify-file", default=None, help="校验指定备份 zip（优先级高于 --verify）")
    args = parser.parse_args(argv)

    src = Path(args.source)
    dst = Path(args.dest)

    if args.verify_file:
        return 0 if verify_backup(Path(args.verify_file)) else 1

    if args.verify:
        snaps = sorted(dst.glob(f"{_BACKUP_PREFIX}*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not snaps:
            print(f"[backup] 目录 {dst} 下没有可校验的备份。")
            return 1
        return 0 if verify_backup(snaps[0]) else 1

    if not src.exists():
        print(f"[backup] 源目录不存在: {src}")
        return 1

    make_backup(src, dst, max(1, args.keep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
