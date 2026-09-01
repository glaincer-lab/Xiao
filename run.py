"""开发启动脚本：python run.py"""
from __future__ import annotations

import sys
from pathlib import Path

# 打包态使用内置 embeddable Python（._pth 隔离模式），sys.path 不包含脚本目录，
# 会导致 `import backend` 失败；显式把本脚本所在目录加入 sys.path，
# 保证开发态（venv）与打包态（embeddable）下 backend 包均可被导入。
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn

from backend.config import config

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=config.get("server.host", "127.0.0.1"),
        port=int(config.get("server.port", 8123)),
    )
