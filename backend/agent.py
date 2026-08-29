"""两段式回复 Agent：计划回复 → 工具执行 → 结果回复。

对应交互流程：
  用户说话 → 识别文本 → 阶段A(计划回复："我准备做X") → 工具执行 → 阶段B(结果回复)。
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable

from backend.config import config
from backend.errors import human_reason
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
    "只有用户明确问你的名字时才可以说「小二」。\n"
    "7. 语音操电脑（computer_mouse/computer_type/computer_hotkey/computer_window/screen_look/uia_dump）："
    "点按、打字、热键、关窗这类动作会先走语音审批，用户说允许才执行，被拒绝就停止并向用户说明；"
    "需要坐标的动作先用 screen_look 或 uia_dump 查看屏幕元素位置再操作；"
    "uia_dump 依赖本机 comtypes 支持库，未安装时按工具返回的提示引导用户。"
)

DEFAULT_MAX_HISTORY = 16
DEFAULT_TOOL_ROUNDS = 500


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


def tool_rounds() -> int:
    """单次任务允许的最大工具调用轮数（E3 高级设置）；留空/非法时用默认 500。"""
    for path in ("llm.cloud.tool_rounds", "llm.local.tool_rounds"):
        try:
            v = int(float(config.get(path, 0) or 0))
        except (TypeError, ValueError):
            continue
        if v > 0:
            return v
    return DEFAULT_TOOL_ROUNDS


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

    async def handle(self, text: str, images: list[str] | None = None) -> None:
        self._set_state(State.PROCESSING)
        self._history.append(ChatMessage(role="user", content=text, images=images or None))
        self._trim()
        try:
            await self._run()
        except asyncio.TimeoutError:
            # LLM 超时专享话术：不吞掉，点明是「响应慢」，引导用户重试
            print("[agent] LLM 响应超时")
            try:
                fallback = "我这边响应有点慢，请稍等几秒再试。"
                emit("assistant_result", text=fallback)
                await self._speak(fallback)
                self._history.append(ChatMessage(role="assistant", content=fallback))
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            # 兜底：LLM/工具异常也要把状态机放回 IDLE；原始错误只进日志，用户只听人话（E2c）
            print(f"[agent] 处理失败: {e}")
            emit("log", level="error", message=f"处理失败: {type(e).__name__}: {e}")
            try:
                fallback = f"抱歉，{human_reason(e, default='我出了点问题，请稍后再试')}。"
                emit("assistant_result", text=fallback)
                await self._speak(fallback)
                self._history.append(ChatMessage(role="assistant", content=fallback))
            except Exception:  # noqa: BLE001
                pass
        finally:
            self._trim()
            if self._on_done:
                self._on_done()

    async def _run(self) -> None:
        tools = self._registry.schemas()
        completion = await self._llm.complete(self._messages(), tools=tools)

        # ---- 多轮工具循环（E3：轮数上限见 tool_rounds()，默认 500）----
        limit = tool_rounds()
        used = 0
        plan_said = False
        executed = False

        while completion.tool_calls and used < limit:
            if not plan_said:
                # ---- 阶段A：计划回复（整个任务只播一次）----
                plan = (completion.content or "").strip() or self._default_plan(completion.tool_calls)
                emit("assistant_plan", text=plan)
                await self._speak(plan)
                plan_said = True
            executed = True

            # ---- 执行本轮工具 ----
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
                tool_obj = self._registry.get(name)
                imgs = getattr(tool_obj, "pending_images", None) if tool_obj is not None else None
                if imgs:
                    self._history.append(
                        ChatMessage(
                            role="user",
                            content=f"[{name}] 结果截图已附上，请结合图片继续回答。",
                            images=list(imgs),
                        )
                    )
                    tool_obj.pending_images = None
            used += 1
            if used < limit:
                completion = await self._llm.complete(self._messages(), tools=tools)

        if completion.tool_calls:
            # 轮数用尽模型仍想继续调工具：不带 tools 再问一次，强制文本收尾
            print(f"[agent] 工具调用轮数已达上限（{limit}），先汇报已完成部分")
            emit("log", level="warn", message=f"工具调用轮数已达上限（{limit}），先汇报已完成部分")
            completion = await self._llm.complete(self._messages())

        # ---- 阶段B：结果回复 ----
        result_text = (completion.content or "").strip()
        if executed and not result_text:
            result_text = "这轮先执行到这里，需要继续就说一声。"
        emit("assistant_result", text=result_text)
        await self._speak(result_text)
        self._history.append(ChatMessage(role="assistant", content=result_text))

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
