"""DSH web 流式桥：常驻 `dsh web` 服务 + 回环 WS/HTTP，任务事件实时上报前端。

相比 headless 一次性进程，web 模式带来：
- 工具执行实时上报（tool/call、tool/result → 前端工作台步骤）
- 助手输出流式转发（assistant/chunk → 前端实时输出区）
- 多轮上下文由 DSH 服务端会话记忆承担（替代基类的 _context 打包）

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


class DSHWebBridge(DSHBridge):
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
        self._session_id: str | None = None
        self._active_session: str | None = None
        self._server_proc: asyncio.subprocess.Process | None = None
        self._cancel_requested = False
        self._call_names: dict[str, str] = {}
        self._chunks: list[str] = []
        self._last_message = ""
        self._degraded = False

    async def run(self, task: str, *, grant: set[str] | None = None) -> str:
        if self._degraded:
            return await super().run(task, grant=grant)
        try:
            return await asyncio.wait_for(self._run_web(task), timeout=self._timeout)
        except DSHWebUnavailable:
            if not self._fallback:
                raise
            self._degraded = True
            return await super().run(task, grant=grant)
        except asyncio.TimeoutError:
            self._kick_cancel()
            raise RuntimeError(f"DSH 任务超时（超过 {int(self._timeout)} 秒）") from None
        except WebSocketException:
            raise RuntimeError("DSH web 连接中断，请重试") from None

    def cancel(self) -> None:
        self._cancel_requested = True
        self._kick_cancel()
        super().cancel()

    def reset_context(self) -> None:
        """web 模式多轮上下文由 DSH 服务端会话记忆；丢弃会话 ID 即等同清空。"""
        self._session_id = None
        super().reset_context()

    def shutdown(self) -> None:
        proc = self._server_proc
        self._server_proc = None
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _kick_cancel(self) -> None:
        """尽力通知服务端取消当前轮（独立线程，失败静默；本地标志由泵轮询兜底）。"""
        sid = self._active_session

        def _job() -> None:
            try:
                self._rpc_sync("session.cancel", {"sessionId": sid} if sid else {})
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
        """复用已就绪的 `dsh web`；没有则以 web profile 拉起常驻服务并等就绪。"""
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

    async def _ensure_session(self) -> str:
        if self._session_id:
            return self._session_id
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
        self._session_id = sid
        return sid

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

    async def _pump(self, ws) -> None:
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                if self._cancel_requested:
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
                self._on_event(payload)

    def _emit(self, kind: str, **payload) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(kind, payload)
        except Exception:
            pass

    def _on_event(self, ev: dict) -> None:
        etype = str(ev.get("type") or "")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if etype == "tool/call":
            call_id = str(data.get("callId") or "")
            name = str(data.get("name") or "tool")
            if call_id:
                self._call_names[call_id] = name
            self._emit(
                "work_step", name=name, status="start",
                summary=self._summarize_args(data.get("arguments")),
            )
        elif etype == "tool/result":
            msg = data.get("message") if isinstance(data.get("message"), dict) else {}
            name = self._call_names.get(str(msg.get("callId") or ""), "tool")
            content = msg.get("content")
            summary = " ".join(str(content).split())[:120] if content is not None else ""
            self._emit(
                "work_step", name=name,
                status="error" if msg.get("isError") else "done", summary=summary,
            )
        elif etype == "assistant/chunk":
            piece = self._data_text(data)
            if piece:
                self._chunks.append(piece)
                if len(self._chunks) > 400:
                    self._chunks = self._chunks[-200:]
                self._emit("dsh_chunk", text=self._live_text())
        elif etype == "assistant/message":
            text = self._data_text(data)
            if text:
                self._last_message = text
        elif etype == "turn/end":
            self._finish(data)

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

    def _live_text(self) -> str:
        return "".join(self._chunks)[-2000:]

    def _finish(self, data: dict) -> None:
        reason = data.get("reason")
        if isinstance(reason, dict):
            kind = str(reason.get("kind") or reason.get("reason") or "")
        elif isinstance(reason, str):
            kind = reason
        else:
            kind = ""
        kind = kind.strip().lower()
        if self._cancel_requested or kind in ("aborted", "interrupted"):
            raise DSHCancelled()
        out = ("".join(self._chunks) or self._last_message).strip()
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

    def _reset_run(self) -> None:
        self._cancel_requested = False
        self._active_session = None
        self._call_names = {}
        self._chunks = []
        self._last_message = ""

    async def _run_web(self, task: str) -> str:
        os.makedirs(self._workspace, exist_ok=True)
        self._reset_run()
        await self._ensure_server()
        try:
            return await self._drive(task)
        except DSHWebRpcError:
            self._session_id = None
            self._reset_run()
            return await self._drive(task)

    async def _drive(self, task: str) -> str:
        session_id = await self._ensure_session()
        ws = await self._subscribe(session_id)
        self._active_session = session_id
        try:
            await self._prompt(session_id, task)
            try:
                await self._pump(ws)
            except _TurnDone as done:
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
