"""设备接入层抽象（未来华为智能家居等）。

语音核心不关心具体设备协议。接入华为智慧生活 / HarmonyOS Connect 时，
实现一个 HuaweiAdapter 并注册到 DeviceRegistry 即可；
上层通过 devices.get('huawei').execute(device_id, action, params) 控制设备。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DeviceAdapter(ABC):
    name: str = "base"

    @abstractmethod
    async def list_devices(self) -> list[dict[str, Any]]:
        """返回设备列表，如 [{"id": ..., "name": ..., "type": ...}]。"""

    @abstractmethod
    async def execute(self, device_id: str, action: str, params: dict[str, Any]) -> str:
        """执行设备动作，返回人类可读结果。"""


class DeviceRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, DeviceAdapter] = {}

    def register(self, adapter: DeviceAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> DeviceAdapter | None:
        return self._adapters.get(name)


devices = DeviceRegistry()
