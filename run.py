"""开发启动脚本：python run.py"""
from __future__ import annotations

import uvicorn

from backend.config import config

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=config.get("server.host", "127.0.0.1"),
        port=int(config.get("server.port", 8123)),
    )
