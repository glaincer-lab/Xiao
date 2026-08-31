"""M5-M3 场景编排："我要看电影了"=灯+窗帘+电视多动作序列 → 经 HA scene 一次调用。

首版按 scene 原子化处理（一次调用，非逐设备）；落地后发布 plan.landed 事件。
"""
from __future__ import annotations

from typing import Any


class SceneOrchestrator:
    """场景编排器：scene 原子化（一次调用），非逐设备并发。"""

    def __init__(self, ha_client: Any | None = None, bus: Any | None = None) -> None:
        self._ha_client = ha_client  # call_scene(scene_name) -> bool
        self._bus = bus              # emit(event, payload)

    def trigger_scene(self, scene_name: str) -> dict[str, Any]:
        """触发场景（一次调用，非逐设备）；落地后发布 plan.landed。"""
        if self._ha_client is None:
            return {"status": "fail", "message": "HA 未连接，场景不可用"}
        ok = self._ha_client.call_scene(scene_name)
        if not ok:
            return {"status": "fail", "message": f"场景 {scene_name} 触发失败"}
        if self._bus is not None:
            self._bus.emit("plan.landed", {"方案": scene_name, "落地凭证": "scene"})
        return {"status": "ok", "方案": scene_name}


__all__ = ["SceneOrchestrator"]
