"""/api/config 写入守卫：防任意键注入 + 提权段排除（审计 C2）。

只允许设置页声明过的叶子路径（settings_schema.SCHEMA）；
perms 段（perms.standing_grants 等）为提权路径，禁止经 /api/config 改写
（授权必须走 /api/perms/standing）。

与 backend/main.py 解耦，便于离线单测（不拉 FastAPI/AI 依赖）。
"""
from __future__ import annotations


def flatten_config(updates: dict, prefix: str = ""):
    """把嵌套更新 dict 摊平成叶子点路径（如 llm.provider）。"""
    for k, v in updates.items():
        path = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            yield from flatten_config(v, path)
        else:
            yield path


def allowed_config_paths() -> set[str]:
    from backend.settings_schema import SCHEMA

    return {
        f["path"] for f in SCHEMA
        if isinstance(f.get("path"), str) and not f["path"].startswith("_guide.")
    }


def validate_config_updates(updates: dict) -> str | None:
    """返回错误信息；None 表示校验通过。

    - 命中 perms 段：拒绝（提权，走专用端点）；
    - 命中未在 settings_schema 声明的叶子路径：拒绝（任意键注入）。
    """
    allowed = allowed_config_paths()
    leaves = list(flatten_config(updates))
    sensitive = sorted({p for p in leaves if p == "perms" or p.startswith("perms.")})
    if sensitive:
        return f"配置段 perms 禁止经 /api/config 修改（请走 /api/perms/standing）：{', '.join(sensitive)}"
    unknown = sorted({p for p in leaves if p not in allowed and not p.startswith("perms")})
    if unknown:
        return f"未知的配置项，已拒绝：{', '.join(unknown[:8])}"
    return None
