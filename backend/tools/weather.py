"""天气查询工具（wttr.in，免 API key）。"""
from __future__ import annotations

import asyncio
from urllib.parse import quote

from backend.config import config
from backend.tools.base import Tool


class WeatherTool(Tool):
    name = "weather"
    description = "查询城市当前天气（温度、体感、湿度）；不指定城市时自动定位。"
    parameters = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名，如 北京 / 上海 / 深圳；留空则自动定位"},
        },
        "required": [],
    }

    async def run(self, city: str = "") -> str:
        target = (city or "").strip() or str(config.get("tools.default_city", "") or "").strip()
        return await asyncio.to_thread(self._query, target)

    def _query(self, city: str) -> str:
        import requests

        url = f"https://wttr.in/{quote(city)}?format=j1&lang=zh" if city else "https://wttr.in/?format=j1&lang=zh"
        label = city or "你所在的位置"
        try:
            r = requests.get(
                url,
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
                f"{label}：{desc_text}，气温 {cur['temp_C']}°C，"
                f"体感 {cur['FeelsLikeC']}°C，湿度 {cur['humidity']}%。"
            )
        except Exception as e:  # noqa: BLE001
            return f"查询天气失败：{e}"
