"""云端 MiniCPM-o 一体化引擎（ModelBest Realtime API，WebSocket 全双工）。

协议已按官方示例核实：OpenBMB/MiniCPM-o-Demo 的 examples/realtime/audio_probe.py。
- 端点：wss://{host}/v1/realtime?mode=audio，host 默认 minicpmo45.modelbest.cn
- 流程：收 session.queue_done → 发 session.init（payload.system_prompt）→ 收 session.created
- 发音频：input.append（input.audio = 16k float32 base64，input.force_listen=False）
- 收结果：response.output.delta（kind 在消息顶层）
    kind=text   回复文本增量
    kind=audio  回复语音（24k float32 base64）
    kind=listen 本轮结束
- 结束：response.done（text 为完整回复）/ session.closed；客户端发 session.close 结束

重要语义提醒：本 API 是端到端全双工「对话」，response.output.delta 的 text 是
**助手的回复**，不是用户语音的转写；因此不能当纯「识别(ASR)」用，属「一体化」。
本类按官方示例实现，未在本机实测；鉴权方式官方示例未带 header，仅留可选 api_key。
"""
from __future__ import annotations

import base64
import json
import select
import time

import numpy as np

from backend.asr.base import ASREngine


class MiniCPMCloudASR(ASREngine):
    def __init__(self, on_result, host: str = "minicpmo45.modelbest.cn", api_key: str | None = None, system_prompt: str = "", on_audio=None) -> None:
        super().__init__(on_result)
        self._host = host
        self._api_key = api_key
        self._system_prompt = system_prompt
        self._on_audio = on_audio
        self._ws = None
        self._final_text = ""

    def _handle_event(self, ev: dict) -> None:
        t = ev.get("type", "")
        if t == "response.output.delta":
            kind = ev.get("kind", "")
            if kind == "text":
                text = ev.get("text") or ""
                if text:
                    self._final_text += text
                    self.on_result(False, text)
            elif kind == "audio":
                audio = ev.get("audio")
                if audio and self._on_audio is not None:
                    try:
                        raw = base64.b64decode(audio)
                        samples = np.frombuffer(raw, dtype=np.float32)
                        pcm16 = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
                        self._on_audio(pcm16, 24000)
                    except Exception:
                        pass
            # kind == "listen" 这里忽略
        elif t == "response.done":
            text = ev.get("text") or ""
            if text:
                self._final_text = text
                self.on_result(True, text)

    def _drain(self) -> None:
        """非阻塞读当前可用的消息并处理（流式上抛文本）。"""
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
        url = f"wss://{self._host}/v1/realtime?mode=audio"
        header = {}
        if self._api_key:
            header["Authorization"] = f"Bearer {self._api_key}"
        self._ws = websocket.create_connection(url, header=header, timeout=15)

        # 等排队完成（兼容 session.queue_done / queue_done），再发初始化
        deadline = time.time() + 15
        initialized = False
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
            mt = ev.get("type", "")
            if mt in ("session.queue_done", "queue_done"):
                self._ws.send(json.dumps({
                    "type": "session.init",
                    "payload": {"system_prompt": self._system_prompt or ""},
                }))
            elif mt == "session.created":
                initialized = True
                break

        if not initialized:
            self._ws.settimeout(None)

    def feed(self, pcm: bytes) -> None:
        if self._ws is None:
            return
        # 约定输入 16kHz/16bit/mono PCM，转 float32 后 Base64
        arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        b64 = base64.b64encode(arr.astype("<f4", copy=False).tobytes()).decode("ascii")
        try:
            self._ws.send(json.dumps({
                "type": "input.append",
                "input": {"audio": b64, "force_listen": False},
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
                ws.send(json.dumps({"type": "session.close", "reason": "user_stop"}))
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
