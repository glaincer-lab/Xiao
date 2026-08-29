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
        """把当前配置写回 config.yaml（原子替换，断电/崩溃不留半截 YAML）。"""
        path = ROOT / "config.yaml"
        tmp = path.with_suffix(".yaml.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            yaml.safe_dump(self._data, f, allow_unicode=True, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


config = Config(_load_yaml())


def env(name: str, default: str | None = None) -> str | None:
    """读取环境变量（含 .env）。"""
    return os.environ.get(name, default)


# ---- 引擎默认参数（唯一事实来源）----
# 各工厂 / 注册表 / 前端提示统一从这里读取，避免同一默认值在多处硬编码而漂移。
# 一体化 MiniCPM-o 经本地 vLLM-omni 服务接入（与云 LLM 同为 base_url+model+key 三件套）
OMNI_BASE_URL = "http://localhost:8000/v1"
OMNI_MODEL = "openbmb/MiniCPM-o-4_5"

# 本地 Ollama（OpenAI 兼容端点，无需真实 Key）
OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_MODEL = "qwen2.5:7b"

# 云端 LLM 供应商默认参数：默认 base_url、默认模型、回退用的环境变量 key
# （None 表示仅用配置里的 key，不读环境变量）
LLM_CLOUD_DEFAULTS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-v4-pro", "DEEPSEEK_API_KEY"),
    "dashscope": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus", "DASHSCOPE_API_KEY"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY"),
    "glm": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash", None),
    "kimi": ("https://api.moonshot.cn/v1", "moonshot-v1-8k", None),
}
