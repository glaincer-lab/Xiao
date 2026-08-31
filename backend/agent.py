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
from backend.memory import MemoryStore, memory_store
from backend.memv1.retrieval import build_injection, set_entry_provider, set_vector_retriever
from backend.session.state import State, emit
from backend.tools.base import ToolRegistry
from backend.tts.base import TTSEngine

# M1-A 数据轨（首版用 DataTrack 读会话日志作为 M1 记忆源；读失败时 provider 为空→回退 v3）
_M1_DATATRACK = None  # 惰性初始化，避免导入即创建目录/读盘

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
        memory: MemoryStore | None = None,
    ) -> None:
        self._llm = llm
        self._tts = tts
        self._registry = registry
        self._set_state = set_state or (lambda _s: None)
        self._on_done = on_done
        self._memory = memory if memory is not None else memory_store
        self._history: list[ChatMessage] = []
        self._setup_m1_provider()

    def _setup_m1_provider(self) -> None:
        """接入 M1 记忆源（`memv4.DataTrack` 会话日志轨），作为 `build_injection` 的 provider。

        容错设计：任何读取失败（数据轨未初始化/文件缺失/损坏）都返回空 provider，
        此时 `_messages` 会回退到 v3 `context_text`，现有功能不受影响。
        """
        global _M1_DATATRACK
        if _M1_DATATRACK is None:
            try:
                from backend.memv4 import DataTrack
                _M1_DATATRACK = DataTrack()
            except Exception:  # noqa: BLE001
                _M1_DATATRACK = None
        if _M1_DATATRACK is None:
            return

        def _provider() -> list:
            try:
                # 会话日志轨记录为 [{id, ts, content?, ...}...]，原样交付给 M1-E 读取层
                return list(_M1_DATATRACK.items("session_logs"))
            except Exception:  # noqa: BLE001
                return []

        set_entry_provider(_provider)

        # 向量召回接线：接上后 build_injection 优先四因子向量召回，向量库空/不可用自动降级全量
        try:
            from backend.memv1.vector_store import get_vector_store, make_retriever

            set_vector_retriever(make_retriever(get_vector_store()))
        except Exception:  # noqa: BLE001 - 向量召回不可用则保持全量注入降级
            pass

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
            # 对话结束触发记忆维护（Sleeptime：索引 + 治理 + 巩固节流，后台异步不阻塞）
            try:
                from backend.memv1.maintenance import run_after_turn

                run_after_turn()
            except Exception:  # noqa: BLE001
                pass

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
        """构建消息：系统提示词（并入长期记忆上下文）+ 对话历史。

        记忆注入策略（v4.1.1 接线）：
        - 优先用 M1 的 `build_injection`（跨会话记忆，双轨/任务态/180 天滤镜都在其内）；
        - 若 M1 无可用记忆（build_injection 返回空串）或未接 provider，则回退 v3
          `context_text`（显式「记住…」记忆），确保现有功能不丢。
        """
        sys = system_prompt()
        mem_ctx = ""
        try:
            # 已设 M1 provider：传入最后一条用户消息作为请求分类依据
            user_req = ""
            for m in reversed(self._history):
                if m.role == "user":
                    user_req = m.content or ""
                    break
            mem_ctx = build_injection(user_req) or ""
        except Exception:  # noqa: BLE001
            mem_ctx = ""
        if not mem_ctx:
            mem_ctx = self._memory.context_text()
        if mem_ctx:
            sys = f"{sys}\n\n{mem_ctx}"
        return [ChatMessage(role="system", content=sys)] + list(self._history)

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
