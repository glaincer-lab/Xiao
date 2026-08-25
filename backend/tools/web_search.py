"""联网搜索工具（DuckDuckGo，免 API key）。"""
from __future__ import annotations

import asyncio

from backend.tools.base import Tool


class WebSearchTool(Tool):
    name = "web_search"
    description = "联网搜索网页，返回相关信息的摘要。适合查资料、查新闻、查事实。"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词或问题"},
        },
        "required": ["query"],
    }

    async def run(self, query: str) -> str:
        return await asyncio.to_thread(self._search, query)

    def _search(self, query: str) -> str:
        import requests
        from bs4 import BeautifulSoup

        try:
            r = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                timeout=15,
            )
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            return f"搜索失败：{e}"

        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for res in soup.select(".result")[:5]:
            a = res.select_one(".result__a")
            sn = res.select_one(".result__snippet")
            if a:
                results.append(
                    {
                        "title": a.get_text(strip=True),
                        "snippet": sn.get_text(strip=True) if sn else "",
                    }
                )
        if not results:
            return "没有搜到相关结果。"

        lines = [f"{i + 1}. {x['title']}：{x['snippet']}" for i, x in enumerate(results)]
        return "\n".join(lines)
