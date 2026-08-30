"""T1 全链路超时兜底测试（历史审计 C4/C5）。

覆盖三处超时/异常兜底：
1. edge_tts 的 _synthesize：合成侧 asyncio.wait_for(10s) 包裹，挂起流在 timeout 内抛错；
2. asr/omni 的 stop()：客户端 API 异常时安全返回，不外抛（上层主循环不崩）；
3. wake_omni 的 feed()：asr 识别抛异常时被 catch，不永久阻塞。
"""
from __future__ import annotations

import asyncio
import sys
import types
import unittest

import backend.tts.edge_tts as edge_tts_mod
from backend.asr.omni import OmniASREngine
from backend.audio.wake_omni import OmniWakeDetector
from backend.tts.edge_tts import EdgeTTS, SYNTHESIS_TIMEOUT


class _HangStream:
    """模拟 edge-tts 云端挂起的流：await 永不返回。"""

    def __init__(self) -> None:
        self.cancel_event = asyncio.Event()

    def __aiter__(self) -> "_HangStream":
        return self

    async def __anext__(self) -> dict:
        # 永不返回，测试 wait_for 是否在 timeout 内打断
        await self.cancel_event.wait()
        raise StopAsyncIteration


class _FakeCommunicate:
    """替身 edge_tts.Communicate，stream() 返回会挂起的流。"""

    def __init__(self, text: str, voice: str, rate: str = "+0%") -> None:
        self._text = text
        self._voice = voice
        self._rate = rate
        self._hang = _HangStream()

    def stream(self):
        return self._hang


class TestEdgeTTSTimeout(unittest.TestCase):
    def test_synthesize_hangs_times_out(self) -> None:
        """合成挂起时，asyncio.wait_for 在超时阈值内抛 TimeoutError，不永久阻塞。"""
        engine = EdgeTTS()
        # 缩短超时，避免真实 10s 等待拖慢测试；用后恢复
        original = edge_tts_mod.SYNTHESIS_TIMEOUT
        edge_tts_mod.SYNTHESIS_TIMEOUT = 0.2
        # 替换模块级 edge_tts.Communicate 为挂起替身
        fake_mod = types.ModuleType("edge_tts")
        fake_mod.Communicate = _FakeCommunicate
        sys.modules["edge_tts"] = fake_mod
        try:
            with self.assertRaises(asyncio.TimeoutError):
                asyncio.run(engine._synthesize("你好，测试超时"))
        finally:
            sys.modules.pop("edge_tts", None)
            edge_tts_mod.SYNTHESIS_TIMEOUT = original

    def test_synthesis_timeout_constant_positive(self) -> None:
        """超时常量应为正数。"""
        self.assertGreater(SYNTHESIS_TIMEOUT, 0)


class _FakeClient:
    """替身 OpenAI 客户端，stop() 调用时抛超时异常。"""

    def __init__(self) -> None:
        self.chat = type("Chat", (), {"completions": type("Comp", (), {
            "create": lambda *a, **k: (_ for _ in ()).throw(TimeoutError("omni API timeout"))
        })()})()


class _FakeASR:
    def __init__(self) -> None:
        self.started = 0
        self.fed = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def feed(self, pcm: bytes) -> None:
        self.fed += 1

    def stop(self) -> str:
        self.stopped += 1
        raise RuntimeError("ASR service down")


class TestWakeOmniBounded(unittest.TestCase):
    def test_wake_feed_tolerates_asr_exception(self) -> None:
        """wake_omni.feed 在 asr 抛异常时不崩溃，返回 False（放弃本次唤醒）。"""
        det = OmniWakeDetector("http://x", "m")
        det._asr = _FakeASR()  # 注入会抛异常的 asr
        det._window = 1  # 任意小窗口，触发识别
        result = det.feed(b"abc")  # 不应抛异常
        self.assertFalse(result)

    def test_wake_feed_clears_buffer_on_error(self) -> None:
        """异常后缓冲区清空，不累积导致后续永久卡住。"""
        det = OmniWakeDetector("http://x", "m")
        det._asr = _FakeASR()
        det._window = 1
        det.feed(b"abc")
        self.assertEqual(len(det._buf), 0)


class TestASROmniStopBounded(unittest.TestCase):
    def test_stop_swallows_client_exception(self) -> None:
        """asr/omni stop() 在客户端异常时返回空串，不外抛。"""
        engine = OmniASREngine(lambda f, t: None, "http://x", "m")
        engine._client = _FakeClient()  # 注入抛异常的 client
        engine._buf = bytearray(b"some pcm data")  # 确保不因空 buf 提前返回
        result = engine.stop()  # 不应外抛；即便 _pcm_to_wav 抛异常也应被吞
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
