"""音频管线：麦克风 → 唤醒词 → VAD 断句 → ASR → 交给 Agent。

状态机：IDLE/SLEEPING(监听唤醒词) → LISTENING(听用户) → PROCESSING/EXECUTING/SPEAKING
(Agent 处理与播报) → 回到 LISTENING；会话静音超时 → SLEEPING。
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections import deque

from backend.asr.factory import build_asr
from backend.audio.mic import MicStream
from backend.audio.vad import VADSegmenter
from backend.audio.wake import build_wake_word
from backend.config import config
from backend.bridge.dsh_bridge import DSHCancelled
from backend.errors import reason_from_text
from backend.rules import RuleEngine
from backend.session.state import State, emit
from backend.tools.base import registry as tool_registry


class Pipeline:
    def __init__(self) -> None:
        self._agent = None
        self._tts = None
        self._state = State.IDLE
        self._lock = threading.Lock()
        self._running = False
        self._loop = None

        self._wake_enabled = bool(config.get("wake_word.enabled", True))
        self._wake = build_wake_word() if self._wake_enabled else None
        self._bargein_enabled = bool(config.get("bargein.enabled", True))
        self._wake_keyword = str(config.get("wake_word.keyword", "小二"))
        self._speaking_suppress = False
        self._dismiss_enabled = bool(config.get("dismiss.enabled", True))
        self._dismiss_phrases = [p for p in (config.get("dismiss.phrases", []) or []) if p]
        self._dismiss_reply = config.get("dismiss.reply", "好的，我先退下了，有需要再叫我。")
        self._clear_enabled = bool(config.get("clear_history.enabled", True))
        self._clear_phrases = [p for p in (config.get("clear_history.phrases", []) or []) if p]
        self._clear_reply = config.get("clear_history.reply", "已清空，我们重新开始。")
        self._shutdown_enabled = bool(config.get("shutdown.enabled", True))
        self._shutdown_phrases = [p for p in (config.get("shutdown.phrases", []) or []) if p]
        self._shutdown_ask = config.get("shutdown.ask_reply", "确认要完全关闭我吗？说确认关闭，或者说取消。")
        self._shutdown_confirm = [p for p in (config.get("shutdown.confirm_phrases", []) or []) if p]
        self._shutdown_done = config.get("shutdown.done_reply", "好的，再见。")
        self._shutdown_cancel_reply = config.get("shutdown.cancel_reply", "好的，不关了。")
        self._shutdown_timeout = float(config.get("shutdown.timeout_ms", 5000)) / 1000.0
        self._approval_enabled = bool(config.get("approval.enabled", True))
        self._approval_allow = [p for p in (config.get("approval.allow_phrases", []) or []) if p] or ["允许", "可以", "同意"]
        self._approval_deny = [p for p in (config.get("approval.deny_phrases", []) or []) if p] or ["拒绝", "不行", "取消"]
        self._approval_timeout = float(config.get("approval.timeout_ms", 15000)) / 1000.0
        self._vad = VADSegmenter()
        self._asr = None
        self._router = None
        self._bridge = None
        self._dsh_started_at = None
        self._approval_future = None
        self._approval_action = None
        self._perms = None
        self._tasks = None
        self._rules = RuleEngine()

        # 麦克风电平（RMS）：供前端画真实声线动画
        self._mic_level_acc = 0.0
        self._mic_level_n = 0
        self._last_level_emit = 0.0

        self._in_utterance = False
        chunk_ms = int(config.get("audio.chunk_ms", 30))
        min_speech_ms = int(config.get("vad.min_speech_ms", 300))
        self._pre_roll = deque(maxlen=max(1, min_speech_ms // chunk_ms))
        self._last_active = 0.0
        self._session_timeout = float(config.get("vad.session_timeout_ms", 30000)) / 1000.0
        self._working_status_phrases = [p for p in (config.get("router.working_status_phrases", []) or []) if p] or ["进展", "怎么样", "好了吗", "完成了吗", "做到哪", "状态", "还要多久"]
        self._working_cancel_phrases = [p for p in (config.get("router.working_cancel_phrases", []) or []) if p] or ["取消", "停", "别做了", "算了", "不用做了"]

    def attach(self, agent, tts, router=None, bridge=None, perms=None, tasks=None) -> None:
        self._agent = agent
        self._tts = tts
        self._router = router
        self._bridge = bridge
        self._perms = perms
        self._tasks = tasks

    def set_router_mode(self, mode: str) -> None:
        if self._router is not None:
            self._router.set_mode(mode)
            emit("router_mode", mode=self._router.mode)

    def reload_soft(self) -> None:
        """保存配置后热加载软配置（无需重启的字段）。

        - agent 的 system_prompt / max_history 在每次请求时实时读 config，天然生效
        - 这里重载的是本类构造时缓存、但属于「软配置」的字段
        """
        self._bargein_enabled = bool(config.get("bargein.enabled", True))
        self._wake_keyword = str(config.get("wake_word.keyword", "小二"))
        self._dismiss_enabled = bool(config.get("dismiss.enabled", True))
        self._dismiss_phrases = [p for p in (config.get("dismiss.phrases", []) or []) if p]
        self._dismiss_reply = config.get("dismiss.reply", "好的，我先退下了，有需要再叫我。")
        self._clear_enabled = bool(config.get("clear_history.enabled", True))
        self._clear_phrases = [p for p in (config.get("clear_history.phrases", []) or []) if p]
        self._clear_reply = config.get("clear_history.reply", "已清空，我们重新开始。")
        self._approval_enabled = bool(config.get("approval.enabled", True))
        self._approval_allow = [p for p in (config.get("approval.allow_phrases", []) or []) if p] or ["允许", "可以", "同意"]
        self._approval_deny = [p for p in (config.get("approval.deny_phrases", []) or []) if p] or ["拒绝", "不行", "取消"]
        self._approval_timeout = float(config.get("approval.timeout_ms", 15000)) / 1000.0
        self._working_status_phrases = [p for p in (config.get("router.working_status_phrases", []) or []) if p] or ["进展", "怎么样", "好了吗", "完成了吗", "做到哪", "状态", "还要多久"]
        self._working_cancel_phrases = [p for p in (config.get("router.working_cancel_phrases", []) or []) if p] or ["取消", "停", "别做了", "算了", "不用做了"]
        if self._router is not None:
            self._router.reload_keywords()

    def clear_memory(self) -> None:
        """一键清空对话记忆（Agent 历史 + DSH 多轮上下文）。"""
        if self._agent is not None:
            self._agent.reset()
        if self._bridge is not None:
            self._bridge.reset_context()

    # ---- 状态（线程安全）----
    def set_state(self, s: State) -> None:
        with self._lock:
            if self._state != s:
                self._state = s
                emit("state", state=s.value)

    def on_agent_done(self) -> None:
        self._in_utterance = False
        self._pre_roll.clear()
        self._last_active = time.time()
        self.set_state(State.LISTENING)

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    @property
    def router_mode(self) -> str:
        return self._router.mode if self._router is not None else "auto"

    # ---- 生命周期 ----
    def start(self, loop) -> None:
        self._loop = loop
        self._asr = build_asr(self._asr_result)
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """优雅关停：结束音频线程、取消挂起的审批并释放 ASR 连接（进程退出时调用）。

        MicStream.read 为阻塞读，线程通常在一个音频块（几十毫秒）内退出，
        join 给 2s 余量兜底。
        """
        self._running = False
        fut = self._approval_future
        if fut is not None and not fut.done() and self._loop is not None:
            self._loop.call_soon_threadsafe(fut.set_result, "rejected")
        t = getattr(self, "_thread", None)
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        asr = getattr(self, "_asr", None)
        if asr is not None:
            close = getattr(asr, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    pass

    # ---- 外部控制（前端按钮 / 手动输入）----
    def wake_manually(self) -> None:
        self._on_wake()

    def interrupt(self) -> None:
        """打断当前播报（前端按钮）。"""
        self._interrupt()

    def submit_text(self, text: str) -> None:
        """手动文本输入（测试 / 键盘输入），绕过 ASR。"""
        text = (text or "").strip()
        if not text:
            return
        self._last_active = time.time()
        self._dispatch(text)

    # ---- 内部 ----
    def _on_wake(self) -> None:
        self._in_utterance = False
        self._pre_roll.clear()
        self._last_active = time.time()
        self.set_state(State.LISTENING)
        emit("wake")

    def _interrupt(self) -> None:
        """立刻停止当前播报并进入聆听态。"""
        self._in_utterance = False
        self._pre_roll.clear()
        if self._tts is not None:
            self._tts.stop()
        self._last_active = time.time()
        self.set_state(State.AWAIT_APPROVAL if self._approval_future is not None else State.LISTENING)
        emit("interrupted")

    async def _speak(self, text: str) -> None:
        """播报；若文本含唤醒词，播报期间屏蔽 KWS 打断，避免自听回声自我打断。"""
        self._speaking_suppress = bool(self._wake_keyword and self._wake_keyword in (text or ""))
        engine = self._tts
        await engine.speak(text)
        self._speaking_suppress = False

    def _emit_mic_level(self, chunk: bytes) -> None:
        """顺带算麦克风 RMS 音量，节流后通过事件总线发给前端画声线。

        每读一个音频块就累加平方和，按固定周期（约 100ms）取一次 RMS 上抛，
        避免每个 30ms 块都推一条消息造成前端卡顿。
        """
        import numpy as np

        try:
            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
            self._mic_level_acc += float(np.mean(samples * samples))
            self._mic_level_n += 1
        except Exception:
            return

        now = time.time()
        if now - self._last_level_emit < 0.1:  # 约 100ms 发一次
            return
        self._last_level_emit = now
        rms = float((self._mic_level_acc / max(1, self._mic_level_n)) ** 0.5)
        self._mic_level_acc = 0.0
        self._mic_level_n = 0
        # int16 满幅 32767；正常说话 RMS 约几百~几千，归一化到 0~1 便于前端画图
        level = min(1.0, rms / 8000.0)
        emit("mic_level", level=round(level, 4), rms=round(rms, 1))

    def _run(self) -> None:
        with MicStream() as mic:
            while self._running:
                try:
                    chunk = mic.read()
                except Exception:
                    time.sleep(0.01)
                    continue
                try:
                    self._process_chunk(chunk)
                except Exception:
                    # 任何未捕获异常都不允许杀死音频线程：记录后继续下一帧
                    import traceback
                    emit("log", level="error", message="音频循环出错，已跳过该帧")
                    traceback.print_exc()
                    time.sleep(0.01)

    def _process_chunk(self, chunk: bytes) -> None:
        self._emit_mic_level(chunk)
        s = self.state
        if s in (State.IDLE, State.SLEEPING):
            if self._wake_enabled and self._wake is not None and self._wake.feed(chunk):
                self._on_wake()
        elif s == State.LISTENING:
            self._handle_listening(chunk)
            # 仅当双方都安静（不在语音中）才累计超时；说话期间不计入
            if not self._in_utterance and time.time() - self._last_active > self._session_timeout:
                self._in_utterance = False
                self.set_state(State.SLEEPING)
        elif s == State.CONFIRM_SHUTDOWN:
            self._handle_listening(chunk)
            if not self._in_utterance and time.time() - self._last_active > self._shutdown_timeout:
                self._in_utterance = False
                self._pre_roll.clear()
                self._last_active = time.time()
                self.set_state(State.LISTENING)  # 确认超时，自动取消关闭
        elif s == State.WORKING:
            self._handle_listening(chunk)
            # 长任务无静音超时；结束由 _run_dsh 切回 LISTENING
        elif s == State.AWAIT_APPROVAL:
            self._handle_listening(chunk)
            # 审批超时由 request_approval 的 wait_for 兜底并切回状态
        elif s == State.SPEAKING:
            # 播报中监听打断：命中唤醒词则停播；播报文本含唤醒词时屏蔽（防自听回声）
            if self._bargein_enabled and not self._speaking_suppress and self._wake is not None and self._wake.feed(chunk):
                self._interrupt()
        # PROCESSING/EXECUTING：丢弃音频，避免自听回声

    def _handle_listening(self, chunk: bytes) -> None:
        ev = self._vad.feed(chunk)
        if ev == "start":
            self._last_active = time.time()
            self._in_utterance = True
            self._asr.start()
            for old in self._pre_roll:  # 补上前置缓冲，减少开头截断
                self._asr.feed(old)
            self._pre_roll.clear()
            self._asr.feed(chunk)
        elif self._in_utterance:
            if ev == "end":
                self._in_utterance = False
                text = (self._asr.stop() or "").strip()
                self._last_active = time.time()
                if text:
                    self._dispatch(text)
                # 无有效文本：保持当前状态（含 CONFIRM_SHUTDOWN）
            else:
                self._asr.feed(chunk)
        else:
            self._pre_roll.append(chunk)

    def _dispatch(self, text: str) -> None:
        if self.state == State.CONFIRM_SHUTDOWN:
            self._handle_confirm(text)
            return
        if self.state == State.AWAIT_APPROVAL:
            self._handle_approval(text)
            return
        if self.state == State.WORKING:
            self._handle_working(text)
            return
        # 后台任务进展/取消（聆听态下也能问）
        if self._tasks is not None and self._tasks.active():
            if self._matches(text, self._working_cancel_phrases):
                self._handle_task_cancel(text)
                return
            if self._matches(text, self._working_status_phrases):
                self._handle_task_status()
                return
        if self._is_clear_history(text):
            self._on_clear_history()
            return
        if self._is_dismiss(text):
            self._on_dismiss()
            return
        if self._is_shutdown(text):
            self._on_shutdown()
            return
        hit = self._rules.match(text)
        if hit is not None:
            self._on_rule_hit(hit)
            return
        channel = self._router.route(text) if self._router is not None else "chat"
        if channel == "dsh" and self._bridge is not None and self._loop is not None:
            self._on_dsh_task(text)
        elif self._loop is not None and self._agent is not None:
            asyncio.run_coroutine_threadsafe(self._agent.handle(text), self._loop)

    # ---- 控制指令匹配 ----
    @staticmethod
    def _normalize(s: str) -> str:
        import re
        return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s).lower()

    def _matches(self, text: str, phrases: list[str]) -> bool:
        t = self._normalize(text)
        if not t or not phrases:
            return False
        return any(self._normalize(p) in t for p in phrases)

    def _is_dismiss(self, text: str) -> bool:
        return self._dismiss_enabled and self._matches(text, self._dismiss_phrases)

    def _is_clear_history(self, text: str) -> bool:
        return self._clear_enabled and self._matches(text, self._clear_phrases)

    def _is_shutdown(self, text: str) -> bool:
        return self._shutdown_enabled and self._matches(text, self._shutdown_phrases)

    # ---- L0 规则指令：触发词直接执行内置工具（无 LLM / 无 key 可用，B1）----
    def _on_rule_hit(self, hit: dict) -> None:
        self._in_utterance = False
        self._pre_roll.clear()
        self._last_active = time.time()
        self.set_state(State.SPEAKING)
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._run_rule(hit), self._loop)

    async def _run_rule(self, hit: dict) -> None:
        if hit.get("speak_first"):
            # 锁屏/睡眠这类动作会打断播报：先把话说完再执行
            text = str(hit.get("reply") or "好的。")
            emit("assistant_result", text=text)
            if self._tts is not None:
                await self._speak(text)
            await tool_registry.call(hit["tool"], **(hit.get("kwargs") or {}))
        else:
            result = await tool_registry.call(hit["tool"], **(hit.get("kwargs") or {}))
            text = str(hit.get("reply") or result)
            emit("assistant_result", text=text)
            if self._tts is not None:
                await self._speak(text)
        self._last_active = time.time()
        self.set_state(State.LISTENING)

    # ---- 退下：回待机 + 清空历史 ----
    def _on_dismiss(self) -> None:
        self._in_utterance = False
        self._pre_roll.clear()
        self._last_active = time.time()
        self.set_state(State.SPEAKING)  # 播报期间丢弃麦克风音频，避免回声
        emit("assistant_result", text=self._dismiss_reply)
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._finish_dismiss(), self._loop)

    async def _finish_dismiss(self) -> None:
        if self._agent is not None:
            self._agent.reset()
        if self._bridge is not None:
            self._bridge.reset_context()
        if self._tts is not None:
            await self._speak(self._dismiss_reply)
        self.set_state(State.SLEEPING)

    # ---- 清空历史：留在聆听态继续听 ----
    def _on_clear_history(self) -> None:
        self._in_utterance = False
        self._pre_roll.clear()
        self._last_active = time.time()
        self.set_state(State.SPEAKING)
        emit("assistant_result", text=self._clear_reply)
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._finish_clear_history(), self._loop)

    async def _finish_clear_history(self) -> None:
        if self._agent is not None:
            self._agent.reset()
        if self._bridge is not None:
            self._bridge.reset_context()
        if self._tts is not None:
            await self._speak(self._clear_reply)
        self._last_active = time.time()
        self.set_state(State.LISTENING)

    # ---- 关闭：两步确认 -> 退出程序 ----
    def _on_shutdown(self) -> None:
        self._in_utterance = False
        self._pre_roll.clear()
        self._last_active = time.time()
        self.set_state(State.SPEAKING)
        emit("assistant_result", text=self._shutdown_ask)
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._ask_shutdown(), self._loop)

    async def _ask_shutdown(self) -> None:
        if self._tts is not None:
            await self._speak(self._shutdown_ask)
        self._last_active = time.time()
        self.set_state(State.CONFIRM_SHUTDOWN)

    def _handle_confirm(self, text: str) -> None:
        self._in_utterance = False
        self._pre_roll.clear()
        self._last_active = time.time()
        if self._matches(text, self._shutdown_confirm):
            self.set_state(State.SPEAKING)
            emit("assistant_result", text=self._shutdown_done)
            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(self._do_shutdown(), self._loop)
        else:  # 取消或其它话 -> 取消关闭
            self.set_state(State.SPEAKING)
            emit("assistant_result", text=self._shutdown_cancel_reply)
            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(self._cancel_shutdown(), self._loop)

    async def _cancel_shutdown(self) -> None:
        if self._tts is not None:
            await self._speak(self._shutdown_cancel_reply)
        self._last_active = time.time()
        self.set_state(State.LISTENING)

    async def _do_shutdown(self) -> None:
        if self._agent is not None:
            self._agent.reset()
        if self._tts is not None:
            await self._speak(self._shutdown_done)
        await asyncio.sleep(0.8)  # 等声卡把结束语缓冲放完再退出，避免「关机后声音被掐断」
        emit("app_shutdown")
        import os
        os._exit(0)

    # ---- DSH 干活（A1b 路由模式）----
    def _on_dsh_task(self, text: str) -> None:
        self._in_utterance = False
        self._pre_roll.clear()
        self._last_active = time.time()
        self.set_state(State.SPEAKING)  # 过渡期丢弃音频，避免回声；话术由 _run_dsh 统一播报
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._run_dsh(text), self._loop)

    async def _run_dsh(self, text: str) -> None:
        # ---- 预判授权：任务需要但未常驻授权的权限，先语音问一次 ----
        grant: set[str] = set()
        if self._perms is not None:
            grant = set(self._perms.standing())
            needed = self._perms.needed(text)
            if needed:
                labels = self._perms.labels(sorted(needed))
                prompt = f"这次任务需要{'、'.join(labels)}，允许吗？请说允许，或者拒绝。"
                decision = await self.request_approval(
                    "、".join(labels), prompt=prompt, on_timeout="deferred"
                )
                if decision == "rejected":
                    self.set_state(State.SPEAKING)
                    emit("assistant_result", text="好的，已取消。")
                    if self._tts is not None:
                        await self._speak("好的，已取消。")
                    self._last_active = time.time()
                    self.set_state(State.LISTENING)
                    return
                if decision == "deferred":
                    self._perms.add_deferred(text, needed)
                    self.set_state(State.SPEAKING)
                    emit("assistant_result", text="需要你授权，我先记到工作面板了，回来确认后再执行。")
                    if self._tts is not None:
                        await self._speak("需要你授权，我先记到工作面板了，回来确认后再执行。")
                    self._last_active = time.time()
                    self.set_state(State.LISTENING)
                    return
                grant |= needed  # 当场允许，并入本次授权

        # ---- 提交后台任务：立即返回聆听态，不再阻塞 ----
        if self._tasks is not None:
            self._tasks.submit(text, grant=grant, notify=self._notify_task_done)
            await self._speak_and_listen("好的，我在后台处理，完成会告诉你。")
        else:
            # 兜底：未接任务管理器时退回旧的阻塞路径（正常不会走到）
            result = await self._bridge.run(text, grant=grant)
            await self._speak_and_listen(result or "任务完成。")

    # ---- 语音审批（A2）----
    @staticmethod
    def _approval_decision(text: str, allow: list[str], deny: list[str]) -> str | None:
        """拒绝优先：返回 allowed-once / rejected / None（未识别）。

        拒绝优先是为了避免「不可以」被「可以」误判为允许。
        """
        n = Pipeline._normalize(text)
        if not n:
            return None
        if any(Pipeline._normalize(p) in n for p in deny):
            return "rejected"
        if any(Pipeline._normalize(p) in n for p in allow):
            return "allowed-once"
        return None

    async def request_approval(self, action: str, *, prompt: str | None = None, on_timeout: str = "rejected") -> str:
        """进入语音审批：播报询问 → 聆听 允许/拒绝 → 返回决定。

        由 /api/dsh/approval 端点在事件循环上调用，阻塞等待语音决定；
        词表与 DSH approval 一致：allowed-once（唯一放行）/ rejected / unavailable。
        on_timeout 控制超时返回值：运行时审批默认 rejected，预判审批传 deferred。
        """
        if not self._approval_enabled:
            return "unavailable"
        fut_existing = self._approval_future
        if fut_existing is not None and not fut_existing.done():
            # 单审批槽：已有询问在等语音回答，新请求直接拒绝，避免 future 被覆盖后旧询问悬死
            return "unavailable"
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._approval_future = fut
        self._approval_action = action
        prompt = prompt or f"是否允许执行：{action}？请说允许，或者拒绝。"
        self.set_state(State.SPEAKING)
        emit("assistant_plan", text=prompt)
        if self._tts is not None:
            await self._speak(prompt)
        self.set_state(State.AWAIT_APPROVAL)
        self._last_active = time.time()
        try:
            decision = await asyncio.wait_for(fut, timeout=self._approval_timeout)
        except asyncio.TimeoutError:
            decision = on_timeout  # 运行时审批超时默认拒绝；预判审批超时默认 defer
        finally:
            self._approval_future = None
            self._approval_action = None
            self._in_utterance = False
            self._pre_roll.clear()
            self._last_active = time.time()
            self.set_state(State.WORKING if self._dsh_started_at is not None else State.LISTENING)
        return decision

    def _handle_approval(self, text: str) -> None:
        """聆听态收到一句话，判断允许/拒绝并回填审批 future。"""
        self._in_utterance = False
        self._pre_roll.clear()
        self._last_active = time.time()
        decision = self._approval_decision(text, self._approval_allow, self._approval_deny)
        if decision is None:
            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(self._reask_approval(), self._loop)
            return
        fut = self._approval_future
        if fut is not None and not fut.done() and self._loop is not None:
            self._loop.call_soon_threadsafe(fut.set_result, decision)

    async def _reask_approval(self) -> None:
        emit("assistant_result", text="没听清，请说允许，或者拒绝。")
        self.set_state(State.SPEAKING)  # 重新询问也必须在播报中丢弃音频，否则自家 TTS 会被识别成“拒绝”
        if self._tts is not None:
            await self._speak("没听清，请说允许，或者拒绝。")
        self.set_state(State.AWAIT_APPROVAL)
        self._last_active = time.time()

    def answer_approval(self, decision: str) -> bool:
        """屏幕按钮直接回填语音审批 future（不经过语音识别，规避回声/噪音误判）。

        由 WS 的 approval_answer 消息在事件循环上调用；词表与 DSH approval 一致。
        若当前没有待确认的审批（future 已结束 / 未进入 AWAIT_APPROVAL）则返回 False。
        """
        mapping = {
            "allow": "allowed-once",
            "allowed-once": "allowed-once",
            "allow-once": "allowed-once",
            "reject": "rejected",
            "rejected": "rejected",
            "deny": "rejected",
            "cancel": "rejected",
            "cancelled": "rejected",
        }
        d = mapping.get(str(decision).strip().lower())
        if d is None:
            return False
        fut = self._approval_future
        if fut is not None and not fut.done() and self._loop is not None:
            self._loop.call_soon_threadsafe(fut.set_result, d)
            return True
        return False

    def _handle_working(self, text: str) -> None:
        self._in_utterance = False
        self._pre_roll.clear()
        self._last_active = time.time()
        if self._matches(text, self._working_cancel_phrases):
            if self._bridge is not None:
                self._bridge.cancel()
            reply = "好的，正在停止。"
        elif self._matches(text, self._working_status_phrases):
            elapsed = max(0, int(time.time() - (self._dsh_started_at or time.time())))
            reply = f"还在处理中，已经用了 {elapsed} 秒。"
        else:
            reply = "我还在处理上一个任务，请稍等。"
        emit("assistant_result", text=reply)
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._speak_working(reply), self._loop)

    async def _speak_working(self, reply: str) -> None:
        self.set_state(State.SPEAKING)
        if self._tts is not None:
            await self._speak(reply)
        self._last_active = time.time()
        if self._dsh_started_at is not None:
            self.set_state(State.WORKING)  # 任务仍在进行，回到 WORKING；否则保持 _run_dsh 设置的状态

    # ---- 后台任务通知 / 控制（Phase 7）----
    async def _speak_and_listen(self, text: str) -> None:
        """播报一句话并回到聆听态（后台任务提交/取消/完成等公共收尾）。"""
        self.set_state(State.SPEAKING)
        emit("assistant_result", text=text)
        if self._tts is not None:
            await self._speak(text)
        self._last_active = time.time()
        self.set_state(State.LISTENING)

    async def _notify_task_done(self, task: dict) -> None:
        """后台任务完成/失败时的语音通知；取消不重复播报。"""
        text = str(task.get("text") or "")
        status = task.get("status")
        if status == "done":
            result = str(task.get("result") or "").strip()
            emit("assistant_result", text=result or "（任务完成，无输出）")
            speak = f"「{text}」完成了。"
            if result and len(result) <= 120:
                speak = f"「{text}」完成了：{result}"
        elif status == "failed":
            # 原始错误文本可能含英文堆栈，映射成一句人话再播报；原文在 tasks.py 落日志（E2c）
            speak = f"「{text}」失败了：{reason_from_text(task.get('error'))}"
            emit("assistant_result", text=speak)
        else:
            return
        self.set_state(State.SPEAKING)
        if self._tts is not None:
            await self._speak(speak)
        self._last_active = time.time()
        self.set_state(State.LISTENING)

    def _handle_task_cancel(self, text: str = "") -> None:
        self._in_utterance = False
        self._pre_roll.clear()
        self._last_active = time.time()
        if self._tasks is None:
            reply = "现在没有正在进行的任务。"
        else:
            target, note = self._match_task_target(text)
            if note:
                reply = note
            elif target is not None:
                ok = self._tasks.cancel(target["id"])
                reply = f"好的，「{target['text']}」正在停止。" if ok else "现在没有正在进行的任务。"
            else:
                ok = self._tasks.cancel()
                reply = "好的，正在停止。" if ok else "现在没有正在进行的任务。"
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._speak_and_listen(reply), self._loop)

    def _match_task_target(self, text: str) -> tuple[dict | None, str | None]:
        """从取消话语中解析目标任务：返回 (目标任务, 语音备注)；裸取消返回 (None, None)。"""
        active = self._tasks.active() if self._tasks is not None else []
        if not active:
            return None, None
        import re

        ordinal = self._parse_ordinal(text)
        if ordinal is not None:
            if 1 <= ordinal <= len(active):
                return active[ordinal - 1], None
            return None, f"现在只有 {len(active)} 个任务在进行。"
        m = re.search(r"[「『“\"']([^」』”\"']+)[」』”\"']", text)
        if m:
            key = m.group(1).strip()
            for t in active:
                if key and key in t["text"]:
                    return t, None
            return None, "没有找到这个任务。"
        return None, None

    @staticmethod
    def _parse_ordinal(text: str) -> int | None:
        import re

        m = re.search(r"第\s*([0-9０-９]+|[一二三四五六七八九十两]+)\s*个", text)
        if not m:
            return None
        raw = m.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        if raw.isdigit():
            return int(raw)
        digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if raw == "十":
            return 10
        if "十" in raw:
            left, _, right = raw.partition("十")
            value = (digits.get(left, 1) if left else 1) * 10
            if right:
                value += digits.get(right, 0)
            return value
        return digits.get(raw)

    def _handle_task_status(self) -> None:
        self._in_utterance = False
        self._pre_roll.clear()
        self._last_active = time.time()
        active = self._tasks.active() if self._tasks is not None else []
        if not active:
            reply = "现在没有正在进行的任务。"
        else:
            labels = {"pending": "排队中", "running": "进行中"}
            parts = [f"「{t['text']}」{labels.get(t['status'], t['status'])}" for t in active]
            reply = "正在进行的任务：" + "；".join(parts)
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._speak_and_listen(reply), self._loop)

    def _asr_result(self, is_final: bool, text: str) -> None:
        emit("asr_partial" if not is_final else "asr_final", text=text)
