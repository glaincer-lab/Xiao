"""本地 MiniCPM-o 端到端引擎（vLLM-omni full-duplex realtime，WebSocket）。

协议按 vllm-omni 官方示例核实：
vllm-omni/examples/online_serving/minicpmo/realtime_duplex_demo.py 及其
vllm_omni.experimental.fullduplex.client.RealtimeDuplexClient。

- 端点：ws://{host}/v1/realtime?duplex=1
- 发 session.update（model/modalities/input_audio_format=pcm16/...），等 session.created
- 发音频：input_audio_buffer.append（pcm16 base64 + sample_rate_hz=16000）
- 收文本：response.audio_transcript.delta / response.output_text.delta（delta 字段）
- 收音频：response.audio.delta（delta 字段，pcm16 base64，输出 24k）
- 结束：发 session.close，收 session.closed

注意：该接口为 vllm-omni 实验性 full-duplex，未在本机实测；
实时麦克风的 commit 时机由模型 native duplex 自动决策，此处按官方基础流程实现。
"""
from __future__ import annotations

import base64
import json
import select
import time

from backend.asr.base import ASREngine


class MiniCPMLocalRealtime(ASREngine):
    def __init__(self, on_result, host: str = "localhost:8099", model: str = "openbmb/MiniCPM-o-4_5", on_audio=None, instructions: str | None = None, ref_audio: str | None = None) -> None:
        super().__init__(on_result)
        self._host = host
        self._model = model
        self._on_audio = on_audio
        self._instructions = instructions
        self._ref_audio = ref_audio
        self._ws = None
        self._final_text = ""

    def _handle_event(self, ev: dict) -> None:
        t = ev.get("type", "")
        if t in ("response.audio_transcript.delta", "response.output_text.delta"):
            delta = ev.get("delta")
            if isinstance(delta, str) and delta:
                self._final_text += delta
                self.on_result(False, delta)
        elif t == "response.audio.delta":
            delta = ev.get("delta") or ev.get("audio")
            if isinstance(delta, str) and delta and self._on_audio is not None:
                try:
                    pcm16 = base64.b64decode(delta)
                    if pcm16:
                        self._on_audio(pcm16, 24000)
                except Exception:
                    pass

    def _drain(self) -> None:
        if self._ws is None:
            return
        while True:
            try:
                ready, _, _ = select.select([self._ws.sock], [], [], 0)
            except Exception:
                break
            if not ready:
                break
            try:
                msg = self._ws.recv()
            except Exception:
                break
            if not msg:
                break
            try:
                self._handle_event(json.loads(msg))
            except Exception:
                continue

    def start(self) -> None:
        import websocket

        self._final_text = ""
        url = f"ws://{self._host}/v1/realtime?duplex=1"
        self._ws = websocket.create_connection(url, timeout=15)

        session: dict = {
            "model": self._model,
            "modalities": ["audio", "text"],
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "turn_detection": None,
            "extra_body": {
                "auto_response": True,
                "minicpmo45_native_duplex": True,
                "force_listen_count": 0,
            },
        }
        if self._ref_audio:
            session["ref_audio"] = self._ref_audio
        if self._instructions:
            session["instructions"] = self._instructions
        self._ws.send(json.dumps({"type": "session.update", "session": session}))

        # 等 session.created
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                self._ws.settimeout(max(0.1, deadline - time.time()))
                msg = self._ws.recv()
            except Exception:
                break
            if not msg:
                break
            try:
                ev = json.loads(msg)
            except Exception:
                continue
            if ev.get("type") == "session.created":
                break

    def feed(self, pcm: bytes) -> None:
        if self._ws is None:
            return
        # 约定输入 16kHz/16bit/mono PCM，本地协议就是 pcm16，直接 base64
        b64 = base64.b64encode(pcm).decode("ascii")
        try:
            self._ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": b64,
                "input_audio_format": "pcm16",
                "sample_rate_hz": 16000,
            }))
        except Exception:
            return
        self._drain()

    def stop(self) -> str:
        ws = self._ws
        self._ws = None
        if ws is None:
            return self._final_text
        try:
            try:
                ws.send(json.dumps({"type": "session.close"}))
            except Exception:
                pass
            ws.settimeout(1.0)
            deadline = time.time() + 3.0
            while time.time() < deadline:
                try:
                    msg = ws.recv()
                except Exception:
                    break
                if not msg:
                    break
                try:
                    ev = json.loads(msg)
                except Exception:
                    continue
                if ev.get("type") in ("session.closed", "error"):
                    break
                self._handle_event(ev)
        finally:
            try:
                ws.close()
            except Exception:
                pass
        return self._final_text

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
