"""权限模型：权限分类、任务意图预测、常驻授权、待授权任务。

用户口径（方案 b，默认宽松）：
- network       网络访问        —— 常驻允许
- write_outside 写工作区外      —— 默认问
- delete        删除文件        —— 默认问
- install       安装软件包      —— 默认问
- system        修改系统        —— 默认问

预测是启发式关键词匹配，不保证 100% 命中；真正拦截仍由 DSH 运行时审批兜底。
"""
from __future__ import annotations

import json
import os
import time
import uuid

from backend.config import ROOT, config

# (分类 id, 中文名, 说明) —— 结构与文案稳定，故硬编码；关键词规则放 config.yaml 便于改
CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("network", "网络访问", "联网、抓取网页、调接口"),
    ("write_outside", "写工作区外", "往工作区之外写文件"),
    ("delete", "删除文件", "删除、清理文件"),
    ("install", "安装软件包", "pip / npm 等安装依赖"),
    ("system", "修改系统", "改注册表、服务、系统配置"),
    ("computer", "操电脑", "语音控制鼠标键盘与 GUI 操作"),
)


class Perms:
    def __init__(self) -> None:
        self._standing = {str(c) for c in (config.get("perms.standing_grants", []) or []) if c}
        rules = config.get("perms.rules", {}) or {}
        self._rules = {str(k): [str(x) for x in (v or []) if x] for k, v in rules.items()}
        self._deferred_path = os.path.join(ROOT, str(config.get("perms.deferred_path", "logs/deferred.json")))
        self._deferred = self._load_deferred()

    # ---- 常驻授权 ----
    def standing(self) -> list[str]:
        return sorted(self._standing)

    def is_granted(self, category: str) -> bool:
        return category in self._standing

    def set_granted(self, category: str, granted: bool) -> None:
        if category not in {c for c, _, _ in CATEGORIES}:
            raise ValueError(f"未知权限分类: {category}")
        if granted:
            self._standing.add(category)
        else:
            self._standing.discard(category)
        config.update({"perms": {"standing_grants": sorted(self._standing)}})
        config.save()

    # ---- 预测 ----
    def predict(self, text: str) -> set[str]:
        t = (text or "").lower()
        hit: set[str] = set()
        for cat, kws in self._rules.items():
            if any(k.lower() in t for k in kws):
                hit.add(cat)
        return hit

    def needed(self, text: str) -> set[str]:
        return self.predict(text) - self._standing

    def labels(self, cats) -> list[str]:
        m = {c: label for c, label, _ in CATEGORIES}
        return [m.get(c, c) for c in cats]

    # ---- 待授权任务 ----
    def add_deferred(self, text: str, needed) -> str:
        item = {
            "id": uuid.uuid4().hex[:8],
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "text": text,
            "needed": sorted(needed),
            "status": "pending",
        }
        self._deferred.append(item)
        self._save_deferred()
        return item["id"]

    def get_deferred(self, item_id: str) -> dict | None:
        for it in self._deferred:
            if it.get("id") == item_id:
                return it
        return None

    def decide_deferred(self, item_id: str, approved: bool) -> bool:
        for it in self._deferred:
            if it.get("id") == item_id and it.get("status") == "pending":
                it["status"] = "approved" if approved else "rejected"
                self._save_deferred()
                return True
        return False

    def list_deferred(self, status: str | None = "pending") -> list[dict]:
        out = [d for d in self._deferred if status is None or d.get("status") == status]
        return out[-20:]  # 最近 20 条

    # ---- 持久化 ----
    def _load_deferred(self) -> list[dict]:
        try:
            if os.path.isfile(self._deferred_path):
                with open(self._deferred_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else []
        except Exception as e:  # noqa: BLE001
            print(f"[perms] 读取待授权任务失败（按空处理）: {e}")
        return []

    def _save_deferred(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._deferred_path), exist_ok=True)
            with open(self._deferred_path, "w", encoding="utf-8") as f:
                json.dump(self._deferred, f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            print(f"[perms] 保存待授权任务失败: {e}")
