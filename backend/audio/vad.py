"""VAD 断句：Silero（推荐，ONNX 推理，精准）或 webrtcvad（轻量备选）。

Silero 能更准确区分语音与环境噪声，解决"噪声误触发"和"没说完就打断"。
"""
from __future__ import annotations

import numpy as np

from backend.config import config


class SileroVAD:
    """基于 Silero VAD 模型（onnxruntime 推理）。"""

    def __init__(self) -> None:
        import os

        import onnxruntime as ort

        model_path = self._find_model()
        self._model = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)
        self._sr = np.array(16000, dtype=np.int64)

    @staticmethod
    def _find_model() -> str:
        from backend.config import ROOT

        p = ROOT / "models" / "silero_vad.onnx"
        if not p.exists():
            raise FileNotFoundError(
                f"缺少 Silero VAD 模型：{p}\n"
                "请下载 silero_vad.onnx 到 models/（Silero VAD 非商用免费，不随仓库分发）"
            )
        return str(p)

    def predict(self, pcm: bytes) -> float:
        """返回该帧的语音概率 0~1。"""
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32767.0
        if x.shape[0] == 0:
            return 0.0
        ort_inputs = {"input": x[None, :], "h": self._h, "c": self._c, "sr": self._sr}
        out, self._h, self._c = self._model.run(None, ort_inputs)
        return float(out[0][0])


class VADSegmenter:
    def __init__(self) -> None:
        sample_rate = int(config.get("audio.sample_rate", 16000))
        frame_ms = int(config.get("audio.chunk_ms", 30))
        min_speech_ms = int(config.get("vad.min_speech_ms", 300))
        silence_ms = int(config.get("vad.silence_ms", 1000))
        engine = config.get("vad.engine", "silero")

        self._engine = engine
        if engine == "silero":
            self._vad = SileroVAD()
            self._threshold = float(config.get("vad.silero_threshold", 0.5))
        else:
            import webrtcvad

            self._vad = webrtcvad.Vad(int(config.get("vad.mode", 3)))
            self._threshold = None
            self._energy_threshold = float(config.get("vad.energy_threshold", 300))

        self._frame_bytes = int(sample_rate * frame_ms / 1000) * 2
        self._min_speech = max(1, round(min_speech_ms / frame_ms))
        self._max_silence = max(1, round(silence_ms / frame_ms))
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0

    def feed(self, pcm: bytes) -> str | None:
        """喂入一个 30ms 帧，返回 'start' / 'end' 或 None。"""
        if len(pcm) < self._frame_bytes:
            return None
        frame = pcm[: self._frame_bytes]

        if self._engine == "silero":
            is_speech = self._vad.predict(frame) >= self._threshold
        else:
            is_speech = False
            if self._energy_threshold > 0:
                samples = np.frombuffer(frame, dtype=np.int16).astype(np.float64)
                rms = float(np.sqrt(np.mean(samples * samples)))
                if rms >= self._energy_threshold:
                    try:
                        is_speech = self._vad.is_speech(frame, 16000)
                    except Exception:
                        is_speech = False
            else:
                try:
                    is_speech = self._vad.is_speech(frame, 16000)
                except Exception:
                    return None

        if is_speech:
            self._speech_frames += 1
            self._silence_frames = 0
            if not self._in_speech and self._speech_frames >= self._min_speech:
                self._in_speech = True
                return "start"
        elif self._in_speech:
            self._silence_frames += 1
            if self._silence_frames >= self._max_silence:
                self._in_speech = False
                self._speech_frames = 0
                self._silence_frames = 0
                return "end"
        return None
