"""天气查询工具（wttr.in，免 API key）。"""
from __future__ import annotations

import asyncio
from urllib.parse import quote

from backend.tools.base import Tool


class WeatherTool(Tool):
    name = "weather"
    description = "查询指定城市的当前天气（天气、温度、体感、湿度）。"
    parameters = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名，如 北京 / 上海 / 深圳"},
        },
        "required": ["city"],
    }

    async def run(self, city: str) -> str:
        return await asyncio.to_thread(self._query, city)

    def _query(self, city: str) -> str:
        import requests

        try:
            r = requests.get(
                f"https://wttr.in/{quote(city)}?format=j1&lang=zh",
                headers={"User-Agent": "curl/8.0"},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            cur = data["current_condition"][0]
            desc = cur.get("lang_zh", [{}])
            desc_text = desc[0].get("value") if desc else None
            if not desc_text:
                desc_text = cur.get("weatherDesc", [{}])[0].get("value", "")
            return (
                f"{city}：{desc_text}，气温 {cur['temp_C']}°C，"
                f"体感 {cur['FeelsLikeC']}°C，湿度 {cur['humidity']}%。"
            )
        except Exception as e:  # noqa: BLE001
            return f"查询天气失败：{e}"
