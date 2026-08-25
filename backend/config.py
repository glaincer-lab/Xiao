"""配置加载：.env + config.yaml，统一对外提供配置与密钥访问。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


def _load_yaml() -> dict[str, Any]:
    path = ROOT / "config.yaml"
    if not path.exists():
        raise FileNotFoundError(f"缺少配置文件: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> None:
    """原地深合并 override 到 base。"""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


class Config:
    """字典式配置封装，支持点路径读取（如 config.get('asr.provider')）。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, dotted: str) -> dict[str, Any]:
        v = self.get(dotted, {})
        return v if isinstance(v, dict) else {}

    def get_all(self) -> dict[str, Any]:
        """返回完整配置（深拷贝，避免外部改动污染内存态）。"""
        import copy

        return copy.deepcopy(self._data)

    def update(self, updates: dict[str, Any]) -> None:
        """深合并更新配置（仅内存，配合 save() 落盘）。"""
        _deep_merge(self._data, updates)

    def save(self) -> None:
        """把当前配置写回 config.yaml。"""
        path = ROOT / "config.yaml"
        with path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(self._data, f, allow_unicode=True, sort_keys=False)


config = Config(_load_yaml())


def env(name: str, default: str | None = None) -> str | None:
    """读取环境变量（含 .env）。"""
    return os.environ.get(name, default)
