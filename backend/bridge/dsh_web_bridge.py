"""DSH web 流式桥：常驻 `dsh web` 服务 + 回环 WS/HTTP，任务事件实时上报前端。

相比 headless 一次性进程，web 模式带来：
- 工具执行实时上报（tool/call、tool/result → 前端工作台步骤）
- 助手输出流式转发（assistant/chunk → 前端实时输出区）
- 多轮上下文由 DSH 服务端会话记忆承担（替代基类的 _context 打包）
- 多任务并发：每轮独立会话与事件流（session.subscribe 按会话作用域），
  正常完成的会话进空闲池复用，串行使用时服务端多轮上下文不丢

bridge.mode 配置：
- headless：永远一次性进程（旧行为）
- web：仅用 web 服务，基础设施不可用直接报错
- auto（默认）：优先 web，基础设施不可用自动永久回退 headless

web 模式下预授权清单（XIAO_GRANT）不适用：常驻服务无法按任务变更授权，
按 fail-closed 处理——审批一律转发小二语音链（xiao-approval-bridge 插件）。
回环地址 127.0.0.1 + Host 栅栏沿用 DSH web 自带安全边界，不对外网开放。
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from typing import Callable

import requests
from websockets.exceptions import WebSocketException

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # websockets<13 的 legacy API
    from websockets import connect as ws_connect  # type: ignore[no-redef]

from backend.bridge.dsh_bridge import DSHBridge, DSHCancelled
from backend.config import config


class DSHWebUnavailable(RuntimeError):
    """DSH web 基础设施不可用（未安装 / 未启动 / 连不上）。"""


class DSHWebRpcError(RuntimeError):
    """DSH web RPC 返回业务错误（会话或协议层）。"""


class _TurnDone(Exception):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


class _TurnFailed(RuntimeError):
    pass


class _RunCtx:
    """单轮任务上下文：并发时各轮持有独立的会话、取消标志与流式缓冲。"""

    def __init__(self) -> None:
        self.session_id: str | None = None
        self.cancel_requested = False
        self.call_names: dict[str, str] = {}
        self.chunks: list[str] = []
        self.last_message = ""
        # run（任务轮）唯一标识，与 backend/tasks.py 的 task_id 同为 8 位 hex，供 audit 事实平面按 run 隔离。
        self.run_id: str = uuid.uuid4().hex[:8]


class DSHWebBridge(DSHBridge):
    supports_concurrent = True

    def __init__(
        self,
        *,
        event_sink: Callable[[str, dict], None] | None = None,
        fallback: bool = True,
    ) -> None:
        super().__init__()
        self._event_sink = event_sink
        self._fallback = fallback
        self._web_port = int(config.get("bridge.web_port", 3081) or 3081)
        self._server_proc: asyncio.subprocess.Process | None = None
        self._server_lock = asyncio.Lock()
        self._idle_sessions: list[str] = []
        self._active_ctxs: set[_RunCtx] = set()
        self._degraded = False

    async def run(self, task: str, *, grant: set[str] | None = None) -> str:
        if self._degraded:
            return await super().run(task, grant=grant)
        ctx = _RunCtx()
        self._active_ctxs.add(ctx)
        try:
            return await asyncio.wait_for(self._run_web(task, ctx), timeout=self._timeout)
        except DSHWebUnavailable:
            if not self._fallback:
                raise
            self._degraded = True
            return await super().run(task, grant=grant)
        except asyncio.TimeoutError:
            ctx.cancel_requested = True
            self._kick_cancel(ctx)
            raise RuntimeError(f"DSH 任务超时（超过 {int(self._timeout)} 秒）") from None
        except asyncio.CancelledError:
            ctx.cancel_requested = True
            self._kick_cancel(ctx)
            raise
        except WebSocketException:
            raise RuntimeError("DSH web 连接中断，请重试") from None
        finally:
            self._active_ctxs.discard(ctx)

    def cancel(self) -> None:
        for ctx in list(self._active_ctxs):
            ctx.cancel_requested = True
            self._kick_cancel(ctx)
        super().cancel()

    def reset_context(self) -> None:
        """web 模式多轮上下文由 DSH 服务端会话记忆；清空空闲会话池即等同清空。"""
        self._idle_sessions.clear()
        super().reset_context()

    def shutdown(self) -> None:
        proc = self._server_proc
        self._server_proc = None
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _kick_cancel(self, ctx: _RunCtx) -> None:
        """尽力通知服务端取消该轮（独立线程，失败静默；本地标志由泵轮询兜底）。

        未绑定会话时不发请求：无 sessionId 的 session.cancel 会被服务端
        解释为全量取消，并发时会误伤其他正在运行的任务轮。
        """
        sid = ctx.session_id
        if not sid:
            return

        def _job() -> None:
            try:
                self._rpc_sync("session.cancel", {"sessionId": sid})
            except Exception:
                pass

        threading.Thread(target=_job, daemon=True, name="dsh-web-cancel").start()

    def _rpc_sync(self, method: str, payload: dict | None = None) -> dict:
        """单次 RPC：POST /api/{method}，信封 client-request / server-response。"""
        body = {
            "type": "client-request",
            "rpcId": uuid.uuid4().hex,
            "method": method,
            "payload": payload or {},
        }
        try:
            resp = requests.post(
                f"http://127.0.0.1:{self._web_port}/api/{method}",
                json=body,
                timeout=15,
            )
        except requests.RequestException as e:
            raise DSHWebUnavailable(f"DSH web 服务连接失败（{e.__class__.__name__}）") from e
        if resp.status_code != 200:
            raise DSHWebUnavailable(f"DSH web 服务响应异常（HTTP {resp.status_code}）")
        try:
            data = resp.json()
        except ValueError as e:
            raise DSHWebUnavailable("DSH web 服务响应不是有效 JSON") from e
        if not isinstance(data, dict) or data.get("type") != "server-response":
            raise DSHWebUnavailable("DSH web 服务响应格式异常")
        if data.get("error"):
            raise DSHWebRpcError(self._err_text(data["error"]))
        result = data.get("result")
        if isinstance(result, dict) and result.get("error"):
            raise DSHWebRpcError(self._err_text(result["error"]))
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _err_text(err) -> str:
        if isinstance(err, dict):
            return str(err.get("message") or err.get("error") or err)
        return str(err)

    def _probe(self) -> bool:
        try:
            self._rpc_sync("session.list")
            return True
        except DSHWebRpcError:
            return True
        except Exception:
            return False

    async def _ensure_server(self) -> None:
        """复用已就绪的 `dsh web`；没有则以 web profile 拉起常驻服务并等就绪。

        并发任务共用一把锁：冷启动时只拉起一个服务进程，其余任务等就绪。
        """
        async with self._server_lock:
            if await asyncio.to_thread(self._probe):
                return
            env = os.environ.copy()
            env["XIAO_STEP_DISABLE"] = "1"
            env.pop("XIAO_GRANT", None)
            try:
                self._server_proc = await asyncio.create_subprocess_exec(
                    *self._cmd, "web", "--no-open",
                    "--host", "127.0.0.1", "--port", str(self._web_port),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    cwd=self._workspace,
                    env=env,
                )
            except (OSError, ValueError) as e:
                raise DSHWebUnavailable(f"无法启动 DSH web 服务（{e.__class__.__name__}）") from e
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                if await asyncio.to_thread(self._probe):
                    return
                if self._server_proc.returncode is not None:
                    raise DSHWebUnavailable(f"DSH web 服务启动失败（退出码 {self._server_proc.returncode}）")
                await asyncio.sleep(0.3)
            raise DSHWebUnavailable("DSH web 服务 30 秒内未就绪")

    async def _acquire_session(self) -> str:
        """取会话：空闲池复用（保留服务端多轮上下文），没有则新建。"""
        if self._idle_sessions:
            return self._idle_sessions.pop()
        return await self._create_session()

    async def _create_session(self) -> str:
        result = await asyncio.to_thread(self._rpc_sync, "session.create", {"cwd": self._workspace})
        sid = ""
        for key in ("id", "sessionId"):
            v = result.get(key)
            if isinstance(v, (str, int)) and str(v):
                sid = str(v)
                break
        if not sid and isinstance(result.get("session"), dict):
            sid = str(result["session"].get("id") or "")
        if not sid:
            raise DSHWebUnavailable("DSH web 会话创建失败：响应缺少会话 ID")
        return sid

    def _return_session(self, session_id: str) -> None:
        if len(self._idle_sessions) < 8:
            self._idle_sessions.append(session_id)

    async def _subscribe(self, session_id: str):
        try:
            ws = await ws_connect(
                f"ws://127.0.0.1:{self._web_port}/api/events.mux",
                max_size=8 * 1024 * 1024,
            )
            await ws.send(json.dumps({
                "type": "client-request",
                "rpcId": uuid.uuid4().hex,
                "method": "session.subscribe",
                "payload": {"sessionId": session_id},
            }))
            return ws
        except (OSError, WebSocketException, ValueError) as e:
            raise DSHWebUnavailable(f"DSH web 事件流连接失败（{e.__class__.__name__}）") from e

    async def _prompt(self, session_id: str, task: str) -> None:
        last: DSHWebRpcError | None = None
        for key in ("message", "text", "input", "prompt"):
            try:
                await asyncio.to_thread(
                    self._rpc_sync, "session.prompt", {"sessionId": session_id, key: task}
                )
                return
            except DSHWebRpcError as e:
                last = e
        raise last if last is not None else DSHWebUnavailable("DSH web 请求发送失败")

    async def _pump(self, ws, ctx: _RunCtx) -> None:
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                if ctx.cancel_requested:
                    raise DSHCancelled()
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(frame, dict) or frame.get("type") != "server-request":
                continue
            if frame.get("method") != "session/event":
                continue
            payload = frame.get("payload")
            if isinstance(payload, dict) and isinstance(payload.get("event"), dict):
                payload = payload["event"]
            if isinstance(payload, dict):
                self._on_event(payload, ctx)

    def _emit(self, kind: str, **payload) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(kind, payload)
        except Exception:
            pass

    def _emit_raw(self, etype: str, data: dict, ctx: _RunCtx) -> None:
        """把桥解析出的原始事件（tool/call、tool/result、assistant/chunk、
        assistant/message、turn/end）连同 run_id 转发给 event_sink，
        供 backend/audit 的 append-only fact plane 订阅记录。

        仅做「追加式上抛」，不改任何事件解析逻辑（call_names/chunks/last_message 维持原样）。
        """
        payload = dict(data or {})
        payload["run_id"] = ctx.run_id
        self._emit(etype, **payload)

    def _on_event(self, ev: dict, ctx: _RunCtx) -> None:
        etype = str(ev.get("type") or "")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if etype == "tool/call":
            call_id = str(data.get("callId") or "")
            name = str(data.get("name") or "tool")
            if call_id:
                ctx.call_names[call_id] = name
            self._emit_raw(etype, data, ctx)  # append-only fact plane 订阅原始事件
            self._emit(
                "work_step", name=name, status="start",
                summary=self._summarize_args(data.get("arguments")),
            )
        elif etype == "tool/result":
            msg = data.get("message") if isinstance(data.get("message"), dict) else {}
            name = ctx.call_names.get(str(msg.get("callId") or ""), "tool")
            content = msg.get("content")
            summary = " ".join(str(content).split())[:120] if content is not None else ""
            self._emit_raw(etype, data, ctx)  # append-only fact plane 订阅原始事件
            self._emit(
                "work_step", name=name,
                status="error" if msg.get("isError") else "done", summary=summary,
            )
        elif etype == "assistant/chunk":
            self._emit_raw(etype, data, ctx)  # append-only fact plane 订阅原始事件
            piece = self._data_text(data)
            if piece:
                ctx.chunks.append(piece)
                if len(ctx.chunks) > 400:
                    ctx.chunks = ctx.chunks[-200:]
                self._emit("dsh_chunk", text=self._live_text(ctx))
        elif etype == "assistant/message":
            self._emit_raw(etype, data, ctx)  # append-only fact plane 订阅原始事件
            text = self._data_text(data)
            if text:
                ctx.last_message = text
        elif etype == "turn/end":
            self._emit_raw(etype, data, ctx)  # append-only fact plane 订阅原始事件
            self._finish(data, ctx)

    @staticmethod
    def _summarize_args(args) -> str:
        if isinstance(args, dict):
            try:
                s = " ".join(f"{k}={v}" for k, v in args.items())
            except Exception:
                s = str(args)
        elif isinstance(args, str):
            s = args
        else:
            s = ""
        s = " ".join(s.split())
        return s[:90] + ("…" if len(s) > 90 else "")

    @staticmethod
    def _data_text(data: dict) -> str:
        for key in ("text", "delta", "content", "message"):
            v = data.get(key)
            if isinstance(v, str) and v:
                return v
            if isinstance(v, dict):
                t = v.get("text") or v.get("content")
                if isinstance(t, str) and t:
                    return t
        return ""

    @staticmethod
    def _live_text(ctx: _RunCtx) -> str:
        return "".join(ctx.chunks)[-2000:]

    def _finish(self, data: dict, ctx: _RunCtx) -> None:
        reason = data.get("reason")
        if isinstance(reason, dict):
            kind = str(reason.get("kind") or reason.get("reason") or "")
        elif isinstance(reason, str):
            kind = reason
        else:
            kind = ""
        kind = kind.strip().lower()
        if ctx.cancel_requested or kind in ("aborted", "interrupted"):
            raise DSHCancelled()
        out = ("".join(ctx.chunks) or ctx.last_message).strip()
        if kind == "completed":
            raise _TurnDone(out)
        labels = {
            "blocked": "DSH 任务被阻塞",
            "error": "DSH 任务执行出错",
            "max-tokens": "DSH 输出达到长度上限",
        }
        msg = labels.get(kind, f"DSH 任务异常结束（{kind or '未知原因'}）")
        tail = out[-200:]
        if tail:
            msg += f"，部分输出：{tail}"
        raise _TurnFailed(msg)

    @staticmethod
    def _reset_run(ctx: _RunCtx) -> None:
        ctx.cancel_requested = False
        ctx.call_names = {}
        ctx.chunks = []
        ctx.last_message = ""

    async def _run_web(self, task: str, ctx: _RunCtx) -> str:
        os.makedirs(self._workspace, exist_ok=True)
        self._reset_run(ctx)
        await self._ensure_server()
        try:
            return await self._drive(task, ctx)
        except DSHWebRpcError:
            self._reset_run(ctx)
            return await self._drive(task, ctx)

    async def _drive(self, task: str, ctx: _RunCtx) -> str:
        session_id = await self._acquire_session()
        ws = await self._subscribe(session_id)
        ctx.session_id = session_id
        try:
            await self._prompt(session_id, task)
            try:
                await self._pump(ws, ctx)
            except _TurnDone as done:
                ctx.session_id = None
                self._return_session(session_id)
                out = done.text
                if not out:
                    raise RuntimeError("DSH 未返回内容")
                return out
        finally:
            try:
                await ws.close()
            except Exception:
                pass
        raise RuntimeError("DSH web 事件流中断，任务未完成")
