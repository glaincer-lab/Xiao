"""开发启动脚本：python run.py"""
from __future__ import annotations

import sys
from pathlib import Path

# 打包态使用内置 embeddable Python（._pth 隔离模式），sys.path 不包含脚本目录，
# 会导致 `import backend` 失败；显式把本脚本所在目录加入 sys.path，
# 保证开发态（venv）与打包态（embeddable）下 backend 包均可被导入。
sys.path.insert(0, str(Path(__file__).resolve().parent))

import logging
from logging.handlers import RotatingFileHandler

import uvicorn

from backend.config import ROOT, config


def _setup_logging() -> None:
    """挂轮转文件日志：打包态 stderr 被 Electron 丢弃，必须落盘才可分析。"""
    log_path = ROOT / str(config.get("logging.file", "logs/xiao.log"))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(str(config.get("logging.level", "INFO")).upper())
    root.addHandler(handler)


if __name__ == "__main__":
    _setup_logging()
    uvicorn.run(
        "backend.main:app",
        host=config.get("server.host", "127.0.0.1"),
        port=int(config.get("server.port", 8123)),
    )
