"""V2-A2 流式桥自测：RPC 信封、事件泵、turn 收尾、降级与取消语义。

单端口假 DSH web（FastAPI 承载 POST /api/{method} RPC + WS /api/events.mux 事件流）
起在本地回环随机端口，与真实 `dsh web` 同端口结构一致；
DSHWebBridge 只需把 `_ensure_server` 换成空操作，即可全链路驱动。
"""
from __future__ import annotations

import asyncio
import threading
import time
import unittest
from unittest import mock

import fastapi
import uvicorn

from backend.bridge import DSHBridge, build_bridge
from backend.bridge.dsh_bridge import DSHCancelled
from backend.bridge.dsh_web_bridge import DSHWebBridge, DSHWebRpcError, DSHWebUnavailable


class _FakeRpcError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _FakeDsh:
    """单端口假 DSH web：RPC 走信封应答，事件流按 frames 脚本逐条推送。"""

    def __init__(self, frames=None, *, nested: bool = True) -> None:
        self.handlers: dict = {}
        self.requests: list = []
        self.frames = frames or []
        self.nested = nested
        self.subscribed = threading.Event()
        self.ready = threading.Event()
        self.port = 0
        self.state: dict = {}
        self._server = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="fake-dsh")
        self._thread.start()
        if not self.ready.wait(15):
            raise RuntimeError("假 DSH web 启动超时")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app = fastapi.FastAPI()

        @app.post("/api/{method}")
        async def rpc(method: str, request: fastapi.Request):
            try:
                body = await request.json()
            except Exception:
                body = {}
            self.requests.append({"path": f"/api/{method}", "payload": body.get("payload")})
            rpc_id = str(body.get("rpcId", ""))
            handler = self.handlers.get(f"/api/{method}")
            if handler is None:
                return {"type": "server-response", "rpcId": rpc_id, "error": {"message": f"未知方法 {method}"}}
            try:
                result = handler(body.get("payload") or {})
            except _FakeRpcError as e:
                return {"type": "server-response", "rpcId": rpc_id, "error": {"message": e.message}}
            return {"type": "server-response", "rpcId": rpc_id, "result": result}

        @app.websocket("/api/events.mux")
        async def events(ws: fastapi.WebSocket):
            await ws.accept()
            try:
                await ws.receive_json()
            except Exception:
                return
            self.subscribed.set()
            for frame in self.frames:
                payload = {"event": frame} if self.nested else frame
                await ws.send_json({
                    "type": "server-request",
                    "rpcId": "srv-1",
                    "method": "session/event",
                    "payload": payload,
                })
            while True:
                try:
                    await ws.receive_text()
                except Exception:
                    return

        async def main():
            config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
            server = uvicorn.Server(config)
            self._server = server
            serve_task = asyncio.create_task(server.serve())
            for _ in range(200):
                try:
                    self.port = server.servers[0].sockets[0].getsockname()[1]
                    break
                except Exception:
                    await asyncio.sleep(0.05)
            if not self.port:
                raise RuntimeError("无法获取假服务端口")
            self.ready.set()
            await serve_task

        try:
            loop.run_until_complete(main())
        except Exception:
            pass
        finally:
            try:
                loop.close()
            except Exception:
                pass

    def close(self) -> None:
        server = self._server
        if server is not None:
            server.should_exit = True


class _Base(unittest.TestCase):
    def make_bridge(self, rpc: _FakeDsh, events: list) -> DSHWebBridge:
        b = DSHWebBridge(event_sink=lambda kind, payload: events.append((kind, payload)), fallback=False)
        b._web_port = rpc.port

        async def _noop_ensure() -> None:
            return None

        b._ensure_server = _noop_ensure
        return b

    def make_rpc(self, *, frames=None, nested: bool = True, created=None,
                 prompt_fail_first: bool = False) -> _FakeDsh:
        rpc = _FakeDsh(frames, nested=nested)
        self.addCleanup(rpc.close)
        sids = created or ["s1"]
        state = {"creates": 0, "prompts": 0}

        def _create(payload):
            sid = sids[min(state["creates"], len(sids) - 1)]
            state["creates"] += 1
            return {"id": sid}

        def _prompt(payload):
            state["prompts"] += 1
            if prompt_fail_first and state["prompts"] <= 4:
                raise _FakeRpcError("session 已失效")
            return {}

        rpc.handlers["/api/session.create"] = _create
        rpc.handlers["/api/session.prompt"] = _prompt
        rpc.state = state
        return rpc


class RpcTests(unittest.TestCase):
    def test_roundtrip_and_payload(self) -> None:
        rpc = _FakeDsh()
        self.addCleanup(rpc.close)
        rpc.handlers["/api/session.list"] = lambda payload: {"sessions": []}
        b = DSHWebBridge()
        b._web_port = rpc.port
        self.assertEqual(b._rpc_sync("session.list"), {"sessions": []})
        self.assertEqual(rpc.requests[-1]["path"], "/api/session.list")
        self.assertEqual(rpc.requests[-1]["payload"], {})

    def test_business_error_raises_rpc_error(self) -> None:
        rpc = _FakeDsh()
        self.addCleanup(rpc.close)

        def _boom(payload):
            raise _FakeRpcError("会话不存在")

        rpc.handlers["/api/session.list"] = _boom
        b = DSHWebBridge()
        b._web_port = rpc.port
        with self.assertRaises(DSHWebRpcError):
            b._rpc_sync("session.list")
        self.assertTrue(b._probe())

    def test_unreachable_is_unavailable(self) -> None:
        b = DSHWebBridge()
        b._web_port = 1
        with self.assertRaises(DSHWebUnavailable):
            b._rpc_sync("session.list")
        self.assertFalse(b._probe())


class StreamTests(_Base):
    FRAMES_DONE = [
        {"type": "tool/call", "data": {"callId": "c1", "name": "bash", "arguments": {"command": "ls tmp"}}},
        {"type": "assistant/chunk", "data": {"text": "你好"}},
        {"type": "assistant/chunk", "data": {"text": "，世界"}},
        {"type": "tool/result", "data": {"message": {"callId": "c1", "content": "a.txt\nb.txt", "isError": False}}},
        {"type": "turn/end", "data": {"reason": {"kind": "completed"}}},
    ]

    def test_e2e_stream_and_steps(self) -> None:
        rpc = self.make_rpc(frames=self.FRAMES_DONE)
        events: list = []
        b = self.make_bridge(rpc, events)
        out = asyncio.run(b.run("列一下目录"))
        self.assertEqual(out, "你好，世界")
        self.assertEqual(b._session_id, "s1")
        self.assertTrue(rpc.subscribed.wait(5))
        self.assertEqual([k for k, _ in events], ["work_step", "dsh_chunk", "dsh_chunk", "work_step"])
        self.assertEqual(events[0][1], {"name": "bash", "status": "start", "summary": "command=ls tmp"})
        self.assertEqual(events[1][1], {"text": "你好"})
        self.assertEqual(events[2][1], {"text": "你好，世界"})
        done = events[3][1]
        self.assertEqual(done["name"], "bash")
        self.assertEqual(done["status"], "done")
        self.assertIn("a.txt", done["summary"])
        prompt = [r for r in rpc.requests if r["path"] == "/api/session.prompt"][-1]
        self.assertEqual(prompt["payload"], {"sessionId": "s1", "message": "列一下目录"})

    def test_flat_payload_and_string_reason(self) -> None:
        frames = [
            {"type": "assistant/message", "data": {"text": "好的"}},
            {"type": "turn/end", "data": {"reason": "completed"}},
        ]
        rpc = self.make_rpc(frames=frames, nested=False)
        b = self.make_bridge(rpc, [])
        out = asyncio.run(b.run("干点活"))
        self.assertEqual(out, "好的")

    def test_stale_session_retries_once(self) -> None:
        rpc = self.make_rpc(
            frames=[
                {"type": "assistant/chunk", "data": {"text": "搞定"}},
                {"type": "turn/end", "data": {"reason": {"kind": "completed"}}},
            ],
            created=["s1", "s2"],
            prompt_fail_first=True,
        )
        b = self.make_bridge(rpc, [])
        out = asyncio.run(b.run("干活"))
        self.assertEqual(out, "搞定")
        self.assertEqual(b._session_id, "s2")
        self.assertEqual(rpc.state["creates"], 2)

    def test_error_turn_raises_human_message(self) -> None:
        frames = [
            {"type": "assistant/chunk", "data": {"text": "半截输出"}},
            {"type": "turn/end", "data": {"reason": {"kind": "error"}}},
        ]
        rpc = self.make_rpc(frames=frames)
        b = self.make_bridge(rpc, [])
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(b.run("干活"))
        msg = str(ctx.exception)
        self.assertIn("DSH 任务执行出错", msg)
        self.assertIn("半截输出", msg)

    def test_completed_without_output(self) -> None:
        frames = [{"type": "turn/end", "data": {"reason": {"kind": "completed"}}}]
        rpc = self.make_rpc(frames=frames)
        b = self.make_bridge(rpc, [])
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(b.run("干活"))
        self.assertIn("DSH 未返回内容", str(ctx.exception))

    def test_aborted_turn_raises_cancelled(self) -> None:
        frames = [{"type": "turn/end", "data": {"reason": {"kind": "aborted"}}}]
        rpc = self.make_rpc(frames=frames)
        b = self.make_bridge(rpc, [])
        with self.assertRaises(DSHCancelled):
            asyncio.run(b.run("干活"))

    def test_client_cancel_during_stream(self) -> None:
        frames = [
            {"type": "assistant/chunk", "data": {"text": "第一步"}},
            {"type": "assistant/chunk", "data": {"text": "第二步"}},
        ]
        rpc = self.make_rpc(frames=frames)
        events: list = []
        first_chunk = threading.Event()

        def sink(kind, payload):
            events.append((kind, payload))
            if kind == "dsh_chunk":
                first_chunk.set()

        b = DSHWebBridge(event_sink=sink, fallback=False)
        b._web_port = rpc.port

        async def _noop_ensure() -> None:
            return None

        b._ensure_server = _noop_ensure
        holder: dict = {}

        def job() -> None:
            try:
                holder["out"] = asyncio.run(b.run("长活"))
            except BaseException as e:
                holder["err"] = e

        t = threading.Thread(target=job, daemon=True)
        t.start()
        self.assertTrue(first_chunk.wait(10))
        time.sleep(0.2)
        b.cancel()
        t.join(15)
        self.assertFalse(t.is_alive())
        self.assertIsInstance(holder.get("err"), DSHCancelled)


class FallbackTests(unittest.TestCase):
    def _patched(self):
        calls: list = []

        async def broken_ensure(self_inner) -> None:
            calls.append("ensure")
            raise DSHWebUnavailable("起不来")

        async def fake_headless(self_inner, task, *, grant=None) -> str:
            calls.append(task)
            return f"headless:{task}"

        return calls, broken_ensure, fake_headless

    def test_auto_falls_back_and_sticks(self) -> None:
        calls, broken_ensure, fake_headless = self._patched()
        with mock.patch.object(DSHWebBridge, "_ensure_server", broken_ensure), \
                mock.patch.object(DSHBridge, "run", fake_headless):
            b = DSHWebBridge()
            out = asyncio.run(b.run("任务一"))
            self.assertEqual(out, "headless:任务一")
            self.assertTrue(b._degraded)
            out2 = asyncio.run(b.run("任务二"))
            self.assertEqual(out2, "headless:任务二")
        self.assertEqual(calls, ["ensure", "任务一", "任务二"])

    def test_web_mode_no_fallback(self) -> None:
        calls, broken_ensure, fake_headless = self._patched()
        with mock.patch.object(DSHWebBridge, "_ensure_server", broken_ensure), \
                mock.patch.object(DSHBridge, "run", fake_headless):
            b = DSHWebBridge(fallback=False)
            with self.assertRaises(DSHWebUnavailable):
                asyncio.run(b.run("任务"))
        self.assertEqual(calls, ["ensure"])


class WiringTests(unittest.TestCase):
    def test_base_shutdown_noop(self) -> None:
        DSHBridge().shutdown()

    def test_web_reset_context_drops_session(self) -> None:
        b = DSHWebBridge()
        b._session_id = "s9"
        b.reset_context()
        self.assertIsNone(b._session_id)

    def test_build_bridge_modes(self) -> None:
        with mock.patch("backend.bridge.config") as cfg:
            cfg.get.return_value = "headless"
            self.assertNotIsInstance(build_bridge(), DSHWebBridge)
            cfg.get.return_value = "auto"
            b = build_bridge()
            self.assertIsInstance(b, DSHWebBridge)
            self.assertTrue(b._fallback)
            cfg.get.return_value = "web"
            b = build_bridge()
            self.assertIsInstance(b, DSHWebBridge)
            self.assertFalse(b._fallback)


if __name__ == "__main__":
    unittest.main()
