"""OpenAI 兼容 LLM 客户端：覆盖 DeepSeek / 通义(百炼兼容模式) / Ollama 等。"""
from __future__ import annotations

from backend.llm.base import ChatMessage, Completion, LLMClient


class OpenAICompatClient(LLMClient):
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        temperature: float = 0.3,
    ) -> None:
        from openai import AsyncOpenAI  # 延迟导入

        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key or "EMPTY")
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

        resp = await self._client.chat.completions.create(**kwargs)
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
