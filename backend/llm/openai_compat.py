"""OpenAI 兼容 LLM 客户端：覆盖 DeepSeek / 通义(百炼兼容模式) / Ollama 等。"""
from __future__ import annotations

import asyncio

from backend.llm.base import ChatMessage, Completion, LLMClient


class OpenAICompatClient(LLMClient):
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        temperature: float = 0.3,
        timeout: float | None = None,
    ) -> None:
        from openai import AsyncOpenAI  # 延迟导入

        from backend.config import config

        # 超时兜底：默认 60s，可经 llm.timeout_sec 配置；防止云端/本地 LLM 无响应时卡死整条语音管线
        if timeout is None:
            try:
                timeout = float(config.get("llm.timeout_sec", 60) or 60)
            except (TypeError, ValueError):
                timeout = 60.0
        self._timeout = timeout
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "EMPTY",
            timeout=self._timeout,
        )
        self._model = model
        self._temperature = temperature

    async def complete(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ) -> Completion:
        kwargs: dict = {
            "model": self._model,
            "messages": [m.to_dict() for m in messages],
            "temperature": self._temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # 双重超时保护：客户端自带 timeout + 外层 wait_for，确保任何挂起都能被中断
        resp = await asyncio.wait_for(
            self._client.chat.completions.create(**kwargs),
            timeout=self._timeout,
        )
        msg = resp.choices[0].message

        tool_calls: list[dict] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                # arguments 保持 JSON 字符串（OpenAI 线格式），便于后续原样回传
                tool_calls.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                    }
                )

        return Completion(content=msg.content or "", tool_calls=tool_calls)
