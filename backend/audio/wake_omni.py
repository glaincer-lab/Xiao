"""一体化唤醒：通过 vLLM-omni（MiniCPM-o）端点周期转写，检测唤醒词。

复用 llm.omni 的端点；每满 window 秒转写一次最近音频，命中关键词即唤醒。
注意：vLLM 转写有延迟，本引擎供一体化语音场景接入。
"""
from __future__ import annotations

from backend.asr.omni import OmniASREngine


class OmniWakeDetector:
    def __init__(self, base_url: str, model: str, api_key: str | None = None, keyword: str = "小二", window_sec: float = 2.0) -> None:
        self._keyword = keyword
        self._hit = False
        self._window = int(window_sec * 16000 * 2)  # 16kHz int16 字节数
        self._buf = bytearray()
        self._asr = OmniASREngine(self._on_result, base_url, model, api_key)

    def _on_result(self, is_final: bool, text: str) -> None:
        if self._keyword in (text or ""):
            self._hit = True

    def feed(self, pcm: bytes) -> bool:
        self._buf += pcm
        if len(self._buf) >= self._window:
            # 超时/异常兜底（C4）：omni 服务挂或转写失败时，放弃本次唤醒并记日志，
            # 主循环不崩、不永久阻塞（不引入跨引擎切换，见结构规划 §5）。
            try:
                self._asr.start()
                self._asr.feed(bytes(self._buf))
                self._asr.stop()
            except Exception as exc:  # noqa: BLE001
                import logging

                logging.getLogger(__name__).warning("wake-omni recognition failed: %s", exc)
            finally:
                self._buf = bytearray()
            if self._hit:
                self._hit = False
                return True
        return False

    def close(self) -> None:
        pass
