"""时间与汇率工具：查时间/日期走本地时钟；查汇率走 open.er-api.com（免 key）。"""
from __future__ import annotations

import asyncio
import datetime as _dt

from backend.tools.base import Tool

_WEEK = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

_CURRENCY_NAMES = {
    "USD": "美元",
    "EUR": "欧元",
    "JPY": "日元",
    "HKD": "港币",
    "GBP": "英镑",
    "KRW": "韩元",
    "RUB": "卢布",
    "AUD": "澳元",
    "CAD": "加元",
    "SGD": "新加坡元",
    "THB": "泰铢",
    "INR": "卢比",
    "CNY": "人民币",
}


class TimeTool(Tool):
    name = "time_now"
    description = "查询今天的日期、星期和当前时间。"
    parameters = {"type": "object", "properties": {}, "required": []}

    async def run(self) -> str:
        now = _dt.datetime.now()
        return (
            f"今天是{now.year}年{now.month}月{now.day}日，{_WEEK[now.weekday()]}，"
            f"现在时间 {now.hour:02d}:{now.minute:02d}。"
        )


class RateTool(Tool):
    name = "exchange_rate"
    description = "查询一种外币兑人民币的实时汇率（免 API key）。"
    parameters = {
        "type": "object",
        "properties": {
            "base": {"type": "string", "description": "外币代码，如 USD / EUR / JPY"},
        },
        "required": [],
    }

    async def run(self, base: str = "USD") -> str:
        code = (base or "USD").strip().upper()
        return await asyncio.to_thread(self._query, code)

    def _query(self, code: str) -> str:
        import requests

        name = _CURRENCY_NAMES.get(code, code)
        try:
            r = requests.get(f"https://open.er-api.com/v6/latest/{code}", timeout=15)
            r.raise_for_status()
            rate = (r.json().get("rates") or {}).get("CNY")
        except Exception:  # noqa: BLE001
            return "查汇率失败：网络不通或稍后再试。"
        if rate is None:
            return f"暂时查不到{name}兑人民币的汇率，稍后再试试。"
        return f"1{name} ≈ {float(rate):.2f} 人民币。"
