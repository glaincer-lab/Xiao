"""M5-M1 Home Assistant 主路径接入（层1，唯一推荐）。

感知先于行动 → 审批分级（建议/确认/白名单自动）→ 执行 → 读回验证（5s）→ 播报。
token 走 env/config 可配，不硬编码；设备白名单自动（安全提权项，白名单外转建议）。
"""
from __future__ import annotations

import os
from typing import Any

READBACK_SECONDS: float = 5.0
READBACK_MISMATCH_MSG: str = "好像没成功，你再看看？"


class HAClient:
    """HA REST/WebSocket 客户端抽象（token 走 env；真实网络层后续接入）。"""

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = base_url or os.getenv("HA_BASE_URL", "")
        self.token = token or os.getenv("HA_TOKEN", "")

    def get_state(self, entity_id: str) -> Any:
        """查设备状态（感知先于行动）。真实实现走 HA REST。"""
        raise NotImplementedError("真实 HA REST 接入后续实现")

    def call_service(self, entity_id: str, action: str) -> bool:
        """执行动作。真实实现走 HA REST/WebSocket。"""
        raise NotImplementedError

    def readback(self, entity_id: str, timeout: float = READBACK_SECONDS) -> Any:
        """执行后读回状态（读回验证）。"""
        raise NotImplementedError


class HAActionCoordinator:
    """HA 主路径编排器：感知 → 判定 → 执行 → 读回 → 播报。"""

    def __init__(self, client: Any, whitelist: list[str] | None = None, bus: Any | None = None) -> None:
        self._client = client
        self._whitelist = set(whitelist or [])
        self._bus = bus

    def execute(self, entity_id: str, action: str, confirm: bool = False) -> dict[str, Any]:
        """执行一条设备操作。白名单内自动；白名单外无确认转建议（不执行）。"""
        # ① 感知先于行动：执行前必有 pre_state
        pre_state = self._client.get_state(entity_id)
        if pre_state is None:
            return {"status": "offline", "message": f"设备 {entity_id} 离线，请检查 HA 连接后重试。"}
        # ② 审批分级：白名单外主动执行=0（转建议，需确认）
        if entity_id not in self._whitelist and not confirm:
            return {"status": "suggest", "message": f"建议：{action} {entity_id}（白名单外，确认后执行）"}
        # ③ 执行
        ok = self._client.call_service(entity_id, action)
        if not ok:
            return {"status": "fail", "message": f"执行失败：{entity_id} {action}"}
        # ④ 读回验证（5s）
        post_state = self._client.readback(entity_id, timeout=READBACK_SECONDS)
        if post_state is None or post_state == pre_state:
            return {"status": "mismatch", "message": READBACK_MISMATCH_MSG}
        # ⑤ 事件发布（device.state_changed 已登记，只发布）
        if self._bus is not None:
            self._bus.emit("device.state_changed", {"entity": entity_id, "前后态": (pre_state, post_state)})
        return {"status": "ok", "pre_state": pre_state, "post_state": post_state}


__all__ = ["HAClient", "HAActionCoordinator", "READBACK_SECONDS", "READBACK_MISMATCH_MSG"]
