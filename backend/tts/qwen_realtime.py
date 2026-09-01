"""TTS：阿里云实时流式播报（qwen3-tts-flash-realtime）+ sounddevice 流式播放。

与整段合成完再播的非流式不同，这里走 Realtime WebSocket：
commit 文本后服务端持续推送 PCM 音频帧（base64），边收边播，首包约 0.4s，
语音能紧跟字幕。API Key 复用阿里云百炼的 DASHSCOPE_API_KEY。

音色体系为英文名：Ethan / Serena / Moon 等英文名音色。

延迟优化：speak 结束后后台预热下一条 WebSocket 连接（建连约 0.9s），
下次播报直接复用，首音延迟约 0.4s。
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import queue
import threading
import wave

from backend.tts.base import TTSEngine

logger = logging.getLogger(__name__)

MODEL = "qwen3-tts-flash-realtime"
URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
SAMPLE_RATE = 24000  # PCM_24000HZ_MONO_16BIT


class _Callback:
    """把 SDK 的 WebSocket 线程回调转投到当前播报会话的队列/事件上。"""

    def __init__(self, engine: "QwenRealtimeTTS") -> None:
        self._e = engine

    def on_open(self) -> None:
        pass

    def on_close(self, close_status_code, close_msg) -> None:
        # 连接被服务端断开（Key 无效/网络中断）也必须置位结束事件，否则播报循环会空转卡死
        d = self._e._done
        if d is not None:
            d.set()

    def on_event(self, message) -> None:
        e = self._e
        if not isinstance(message, dict):
            return
        t = message.get("type")
        if t == "response.audio.delta":
            q = e._q
            if q is not None:
                try:
                    q.put(base64.b64decode(message.get("delta") or ""))
                except Exception:
                    pass
        elif t in ("response.done", "error", "session.finished"):
            d = e._done
            if d is not None:
                d.set()


class QwenRealtimeTTS(TTSEngine):
    """阿里云实时流式播报（首包约 0.4s，边合成边播）。"""

    def __init__(self, voice: str = "Ethan", api_key: str | None = None) -> None:
        self._voice = voice
        self._api_key = api_key
        self._stop_requested = False
        self._closed = False
        self._q: "queue.Queue[bytes] | None" = None
        self._done: threading.Event | None = None
        self._stream = None
        self._active_rt = None
        self._ready_rt = None          # 预热的连接（已 connect + update_session）
        self._rewarming = False
        self._lock = threading.Lock()

    # ---- 连接管理 ----
    def _new_rt(self):
        import dashscope
        from dashscope.audio.qwen_tts_realtime import AudioFormat, QwenTtsRealtime

        key = self._api_key or os.environ.get("DASHSCOPE_API_KEY")
        if key:
            dashscope.api_key = key
        rt = QwenTtsRealtime(model=MODEL, callback=_Callback(self), url=URL)
        rt.connect()
        rt.update_session(
            voice=self._voice,
            response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
            mode="commit",
        )
        return rt

    def preflight(self) -> str | None:
        key = self._api_key or os.environ.get("DASHSCOPE_API_KEY")
        if not key:
            return "缺少阿里云百炼 API Key（DASHSCOPE_API_KEY）：请编辑该方案填入"
        return None

    def warm(self) -> None:
        """后台预热一条连接（建连约 0.9s，放到启动线程避免首句卡顿）。"""
        if self._closed:
            return
        with self._lock:
            if self._ready_rt is not None or self._rewarming:
                return
            self._rewarming = True
        threading.Thread(target=self._do_warm, daemon=True).start()

    def _do_warm(self) -> None:
        try:
            rt = self._new_rt()
            with self._lock:
                if self._closed:  # close() 抢在预热前：丢掉这条连接，别泄漏
                    self._ready_rt = None
                    orphan = rt
                else:
                    self._ready_rt = rt
                    orphan = None
            if orphan is not None:
                try:
                    orphan.close()
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001
            logger.warning("Qwen 实时流式连接预热失败：%s", e)
        finally:
            self._rewarming = False

    def _take_ready(self):
        """取预热的连接；闲置断开则丢弃（现场重连兜底）。"""
        with self._lock:
            rt = self._ready_rt
            self._ready_rt = None
        if rt is not None:
            ws = getattr(rt, "ws", None)
            if not (ws and ws.sock and ws.sock.connected):
                try:
                    rt.close()
                except Exception:
                    pass
                rt = None
        return rt

    # ---- 播报 ----
    def stop(self) -> None:
        """立刻停止当前播报（打断用）：静音 + 中止服务端合成。"""
        self._stop_requested = True
        stream = self._stream
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
        rt = self._active_rt
        if rt is not None:
            try:
                rt.cancel_response()
            except Exception:
                pass

    def close(self) -> None:
        """释放连接（换方案/退出时调用）。"""
        self._stop_requested = True
        self._closed = True
        with self._lock:
            rt = self._ready_rt
            self._ready_rt = None
        for r in (rt, self._active_rt):
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass

    async def speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._stop_requested = False
        await asyncio.to_thread(self._speak_blocking, text)

    audio_ext = ".wav"

    def cache_fingerprint(self) -> str:
        return f"qwen_rt|{self._voice}"

    async def synthesize(self, text: str) -> bytes:
        """只合成不播放（试听缓存用）：收集实时流 PCM 帧封装为 WAV 字节。"""
        text = (text or "").strip()
        if not text:
            return b""
        return await asyncio.to_thread(self._synthesize_blocking, text)

    def _synthesize_blocking(self, text: str) -> bytes:
        """一次性会话收集实时流音频：不经过扬声器，PCM → WAV 字节，失败抛异常。"""
        rt = self._take_ready()
        if rt is None:
            rt = self._new_rt()
        q: "queue.Queue[bytes]" = queue.Queue()
        done = threading.Event()
        self._q = q
        self._done = done
        self._active_rt = rt
        self._last_error = None
        pcm_buf = bytearray()
        try:
            rt.append_text(text)
            rt.commit()
            idle_ticks = 0
            while True:
                if self._stop_requested:
                    raise RuntimeError("合成已被中止")
                try:
                    pcm = q.get(timeout=0.05)
                except queue.Empty:
                    if done.is_set():
                        break
                    idle_ticks += 1
                    if idle_ticks > 300:  # 15s 无任何音频：连接假死（Key 无效/网络断），中断兜底
                        raise RuntimeError("15 秒未收到音频（连接可能已断开或 Key 无效）")
                    continue
                idle_ticks = 0
                pcm_buf.extend(pcm)
        finally:
            try:
                rt.close()  # 本轮会话结束，服务端资源一并释放
            except Exception:
                pass
            self._active_rt = None
            self._q = None
            self._done = None
            self.warm()  # 预热下一条连接，下次播报首音更快
        if not pcm_buf:
            raise RuntimeError("Qwen 实时流式未返回音频")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # int16
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(bytes(pcm_buf))
        return buf.getvalue()

    def _speak_blocking(self, text: str) -> None:
        import numpy as np
        import sounddevice as sd

        rt = self._take_ready()
        if rt is None:
            rt = self._new_rt()  # 现场建连（约 0.9s），正常已被预热

        q: "queue.Queue[bytes]" = queue.Queue()
        done = threading.Event()
        self._q = q
        self._done = done
        self._active_rt = rt

        stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16")
        self._stream = stream
        stream.start()
        try:
            rt.append_text(text)
            rt.commit()
            idle_ticks = 0
            while True:
                if self._stop_requested:
                    break
                try:
                    pcm = q.get(timeout=0.05)
                except queue.Empty:
                    if done.is_set():
                        break
                    idle_ticks += 1
                    if idle_ticks > 300:  # 15s 无任何音频：连接假死（Key 无效/网络断），中断兜底
                        self._last_error = "15 秒未收到音频（连接可能已断开或 Key 无效）"
                        logger.warning("Qwen 实时流式超过 15s 未收到音频，中断本次播报")
                        break
                    continue
                idle_ticks = 0
                stream.write(np.frombuffer(pcm, dtype=np.int16))  # 阻塞写（背压），按播放节奏消费
            if self._stop_requested:
                try:
                    rt.cancel_response()
                except Exception:
                    pass
            stream.stop()
        except Exception as e:  # noqa: BLE001
            self._last_error = str(e)  # 播报吞异常保持主流程稳健，试听端点据实回报
            logger.warning("Qwen 实时流式播报失败：%s", e)
        finally:
            try:
                stream.close()
            except Exception:
                pass
            try:
                rt.close()  # 本轮会话结束，服务端资源一并释放
            except Exception:
                pass
            self._stream = None
            self._active_rt = None
            self._q = None
            self._done = None
            self.warm()  # 预热下一条连接，下次播报首音更快
