"""麦克风采集：持续读取 16kHz / 16bit / mono 的 PCM 块。"""
from __future__ import annotations

import sounddevice as sd

from backend.config import config


class MicStream:
    def __init__(self) -> None:
        self.sample_rate = int(config.get("audio.sample_rate", 16000))
        chunk_ms = int(config.get("audio.chunk_ms", 30))
        self.chunk = int(self.sample_rate * chunk_ms / 1000)  # 每块样本数
        device = config.get("audio.input_device", None)
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.chunk,
            device=device,
        )

    def __enter__(self) -> "MicStream":
        self._stream.start()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def read(self) -> bytes:
        """阻塞读取一个音频块，返回 int16 PCM bytes。"""
        data, _ = self._stream.read(self.chunk)
        return data.tobytes()

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
