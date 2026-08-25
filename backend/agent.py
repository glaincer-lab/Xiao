"""两段式回复 Agent：计划回复 → 工具执行 → 结果回复。

对应交互流程：
  用户说话 → 识别文本 → 阶段A(计划回复："我准备做X") → 工具执行 → 阶段B(结果回复)。
"""
from __future__ import annotations

import json
from typing import Callable

from backend.config import config
from backend.llm.base import ChatMessage, LLMClient
from backend.session.state import State, emit
from backend.tools.base import ToolRegistry
from backend.tts.base import TTSEngine

DEFAULT_SYSTEM_PROMPT = (
    "你是「小二」，运行在 Windows 上的中文语音工作助手。\n"
    "规则：\n"
    "1. 始终用简洁、口语化的中文回复，适合语音播报；不要用列表、Markdown、代码块、表情符号，句子要短。\n"
    "2. 当需要执行任务（联网搜索/查资料、打开网址或应用、查天气、设置提醒等）时，"
    "先输出一句简短的话说明你准备做什么（例如“好的，我来帮你查一下今天的天气”），然后调用对应工具。\n"
    "3. 工具执行完成后，用一句话汇报结果。\n"
    "4. 普通对话直接回答，不要调用工具。\n"
    "5. 用户的话已由语音识别转写，可能有少量同音错字，请结合上下文理解意图。\n"
    "6. 日常自称用「我」，不要主动说出「小二」这三个字（会触发语音唤醒打断自己）；"
    "只有用户明确问你的名字时才可以说「小二」。"
)

DEFAULT_MAX_HISTORY = 16


def system_prompt() -> str:
    """读取自定义系统提示词；留空则用内置默认。"""
    s = str(config.get("agent.system_prompt", "") or "").strip()
    return s or DEFAULT_SYSTEM_PROMPT


def max_history() -> int:
    v = config.get("agent.max_history", DEFAULT_MAX_HISTORY)
    try:
        return max(1, int(v))
    except (TypeError, ValueError):
        return DEFAULT_MAX_HISTORY


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        tts: TTSEngine,
        registry: ToolRegistry,
        set_state: Callable[[State], None] | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        self._llm = llm
        self._tts = tts
        self._registry = registry
        self._set_state = set_state or (lambda _s: None)
        self._on_done = on_done
        self._history: list[ChatMessage] = []

    async def handle(self, text: str) -> None:
        self._set_state(State.PROCESSING)
        self._history.append(ChatMessage(role="user", content=text))
        self._trim()

        completion = await self._llm.complete(self._messages(), tools=self._registry.schemas())

        if completion.tool_calls:
            # ---- 阶段A：计划回复 ----
            plan = (completion.content or "").strip() or self._default_plan(completion.tool_calls)
            emit("assistant_plan", text=plan)
            await self._speak(plan)

            # ---- 执行工具 ----
            self._set_state(State.EXECUTING)
            self._history.append(
                ChatMessage(
                    role="assistant",
                    content=completion.content or "",
                    tool_calls=completion.tool_calls,
                )
            )
            for tc in completion.tool_calls:
                name = tc["function"]["name"]
                raw_args = tc["function"].get("arguments") or "{}"
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        args = {}
                else:
                    args = raw_args or {}
                emit("tool_call", name=name, args=args)
                result = await self._registry.call(name, **args)
                emit("tool_result", name=name, summary=str(result)[:200])
                self._history.append(
                    ChatMessage(role="tool", content=result, tool_call_id=tc.get("id", ""), name=name)
                )

            # ---- 阶段B：结果回复 ----
            completion2 = await self._llm.complete(self._messages())
            result_text = (completion2.content or "").strip()
            emit("assistant_result", text=result_text)
            await self._speak(result_text)
            self._history.append(ChatMessage(role="assistant", content=result_text))
        else:
            # ---- 纯对话 ----
            reply = (completion.content or "").strip()
            emit("assistant_result", text=reply)
            await self._speak(reply)
            self._history.append(ChatMessage(role="assistant", content=reply))

        self._trim()
        if self._on_done:
            self._on_done()

    async def _speak(self, text: str) -> None:
        self._set_state(State.SPEAKING)
        await self._tts.speak(text)

    def _messages(self) -> list[ChatMessage]:
        return [ChatMessage(role="system", content=system_prompt())] + list(self._history)

    def _trim(self) -> None:
        limit = max_history()
        if len(self._history) > limit:
            self._history = self._history[-limit:]

    def reset(self) -> None:
        """清空对话历史（退下 / 关闭 / 清空历史时调用）。"""
        self._history.clear()

    @staticmethod
    def _default_plan(tool_calls: list[dict]) -> str:
        names = "、".join(tc["function"]["name"] for tc in tool_calls)
        return f"好的，我来处理（{names}）。"
