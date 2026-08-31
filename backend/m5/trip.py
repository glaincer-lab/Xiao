"""M5-M5 行程/饮食编排（A 型范式）。

澄清 → 查工具（天气/地图/日历，外部失败降级并明说缺什么）→ ≤3 方案各带代价+置信度
→ 敢给推荐 → 落地动作必须明确（写日历/设提醒；建议不落地=白搭，缺则明说）→ 事后复盘入画像。
"""
from __future__ import annotations

from typing import Any

MAX_OPTIONS: int = 3


class TripPlanner:
    """行程编排器（A 型范式）。"""

    def __init__(self, weather: Any | None = None, calendar: Any | None = None) -> None:
        self._weather = weather    # get_forecast(destination, date) -> dict | None
        self._calendar = calendar  # add_event(option) -> bool

    def plan(self, destination: str, date: str, who: str = "") -> dict[str, Any]:
        """生成 ≤3 方案各带代价+置信度；外部天气失败降级并明说。"""
        forecast = self._weather.get_forecast(destination, date) if self._weather is not None else None
        weather_missing = None if forecast else "天气数据缺失"
        options = self._build_options(destination, date, forecast)
        return {"options": options[:MAX_OPTIONS], "weather_missing": weather_missing}

    def recommend(self, options: list[dict]) -> dict | None:
        """敢给推荐：返回置信度最高的方案。"""
        if not options:
            return None
        return max(options, key=lambda o: o.get("置信度", 0.0))

    def land(self, option: dict) -> dict[str, Any]:
        """落地动作必须明确：写日历；缺则明说不落地。"""
        if self._calendar is None:
            return {"landed": False, "message": "建议不落地：未配置日历"}
        self._calendar.add_event(option)
        return {"landed": True, "message": "已写入日历"}

    def _build_options(self, destination: str, date: str, forecast: dict | None) -> list[dict]:
        return [
            {"方案": "A", "代价": "低", "置信度": 0.8, "详情": f"{destination} 室内"},
            {"方案": "B", "代价": "中", "置信度": 0.6, "详情": f"{destination} 半户外"},
            {"方案": "C", "代价": "高", "置信度": 0.4, "详情": f"{destination} 户外"},
        ]


__all__ = ["TripPlanner", "MAX_OPTIONS"]
