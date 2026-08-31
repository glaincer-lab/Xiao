"""M4-M2 首个观察场景：穿搭建议（场景=配置，横向复用同一框架）。

场景模板为配置（OUTFIT_FIELDS / 建议模板 / 降级话术）；后续场景（伤口/快递单/桌面物品）
替换配置即可，无需新开发。
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

# ---- 场景模板（配置） ----
OUTFIT_FIELDS: tuple[str, ...] = ("单品列表", "主色调", "层数")
LOW_CONFIDENCE_FALLBACK: str = "信息不足，建议重拍"


def parse_structured(vlm_output: str) -> dict[str, Any] | None:
    """从 VLM 输出解析结构化字段（优先 JSON，回退键值对）。失败返回 None。"""
    if not vlm_output:
        return None
    text = str(vlm_output).strip()
    # ① JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return {k: data.get(k) for k in OUTFIT_FIELDS if data.get(k)}
    except Exception:  # noqa: BLE001
        pass
    # ② 键值对
    result: dict[str, Any] = {}
    for field in OUTFIT_FIELDS:
        m = re.search(rf"{field}\s*[:：]\s*(.+?)(?=\n|$)", text)
        if m:
            result[field] = m.group(1).strip()
    return result or None


def has_all_fields(structured: Mapping[str, Any] | None) -> bool:
    return structured is not None and all(structured.get(k) for k in OUTFIT_FIELDS)


def build_suggestion(structured: Mapping[str, Any]) -> dict[str, Any]:
    """建议结构：总评一句人话 + 分项（配色/层次/场合匹配）+ 一条可执行建议。"""
    items = str(structured.get("单品列表", ""))
    color = str(structured.get("主色调", ""))
    layers = str(structured.get("层数", ""))
    return {
        "总评": f"这一身以{color}为主，{layers}层次，整体协调。",
        "分项": {
            "配色": color,
            "层次": layers,
            "场合匹配": "日常通勤",
        },
        "可执行建议": f"可将「{items}」中与{color}冲突的单品替换为同色系低饱和色。",
    }


def render_outfit(vlm_output: str) -> dict[str, Any]:
    """场景入口：结构化解析 → 失败按模板重试一次 → 仍失败降级话术。"""
    structured = parse_structured(vlm_output)
    if has_all_fields(structured):
        return build_suggestion(structured)
    retried = parse_structured(_apply_template_hint(vlm_output))
    if has_all_fields(retried):
        return build_suggestion(retried)
    return {"降级": LOW_CONFIDENCE_FALLBACK}


def _apply_template_hint(vlm_output: str) -> str:
    """重试时附加模板提示。"""
    fields = "、".join(OUTFIT_FIELDS)
    return f"{vlm_output}\n{fields}"


__all__ = [
    "OUTFIT_FIELDS",
    "LOW_CONFIDENCE_FALLBACK",
    "parse_structured",
    "has_all_fields",
    "build_suggestion",
    "render_outfit",
]
