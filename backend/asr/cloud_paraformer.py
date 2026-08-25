"""云端流式 ASR：阿里云百炼实时识别（dashscope SDK）。

复用 DASHSCOPE_API_KEY。支持两类模型：
- paraformer-realtime-v2：普通话，16kHz
- fun-asr-flash-8k-realtime：方言（重庆话/四川话/粤语等），8kHz；输入从 16k 自动降采样
"""
from __future__ import annotations

from backend.asr.base import ASREngine


class ParaformerRealtime(ASREngine):
    def __init__(self, on_result, model: str = "paraformer-realtime-v2", sample_rate: int = 16000) -> None:
        super().__init__(on_result)
        self._model_name = model
        self._sample_rate = sample_rate
        self._recognition = None
        self._final_text = ""
        self._frame_ms = 100
        self._frame_bytes = sample_rate * self._frame_ms // 1000 * 2  # 100ms 帧字节数
        self._buf = bytearray()

    def _resample_to(self, pcm: bytes) -> bytes:
        """把 16kHz 输入降到目标采样率（目前仅 8k 降采样）。"""
        if self._sample_rate >= 16000:
            return pcm
        import numpy as np

        arr = np.frombuffer(pcm, dtype=np.int16)
        if self._sample_rate == 8000:
            arr = arr[::2]  # 16k -> 8k：每 2 个采样取 1 个
        return arr.astype(np.int16).tobytes()

    def _make_callback(self):
        outer = self

        class CB:
            def on_open(self) -> None:
                pass

            def on_complete(self) -> None:
                pass

            def on_close(self) -> None:
                pass

            def on_error(self, result) -> None:
                pass

            def on_event(self, result) -> None:
                sentence = result.get_sentence() if hasattr(result, "get_sentence") else None
                if sentence is None:
                    return
                if isinstance(sentence, dict):
                    text = sentence.get("text", "")
                    is_end = bool(sentence.get("sentence_end", False))
                else:
                    text = getattr(sentence, "text", "") or ""
                    is_end = bool(getattr(sentence, "sentence_end", getattr(sentence, "is_sentence_end", False)))
                if not text:
                    return
                if is_end:
                    outer._final_text += text
                    outer.on_result(True, outer._final_text)
                else:
                    outer.on_result(False, text)

        return CB()

    def start(self) -> None:
        from dashscope.audio.asr import Recognition

        self._final_text = ""
        self._buf = bytearray()
        self._recognition = Recognition(
            model=self._model_name,
            format="pcm",
            sample_rate=self._sample_rate,
            callback=self._make_callback(),
        )
        self._recognition.start()

    def feed(self, pcm: bytes) -> None:
        if self._recognition is None:
            return
        pcm = self._resample_to(pcm)
        self._buf += pcm
        while len(self._buf) >= self._frame_bytes:
            frame = bytes(self._buf[: self._frame_bytes])
            del self._buf[: self._frame_bytes]
            self._recognition.send_audio_frame(frame)

    def stop(self) -> str:
        if self._recognition is None:
            return self._final_text
        if self._buf:
            self._recognition.send_audio_frame(bytes(self._buf))
            self._buf = bytearray()
        self._recognition.stop()
        self._recognition = None
        return self._final_text
