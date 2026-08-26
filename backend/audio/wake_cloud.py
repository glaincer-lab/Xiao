"""云端方言唤醒：用阿里云方言 ASR（fun-asr-flash-8k-realtime）持续识别，检测唤醒词。

接口与本地 WakeWordDetector 一致：feed(pcm) -> bool（是否命中）。
注意：持续云端识别有网络成本与延迟；默认仍用本地 sherpa，本引擎供方言唤醒场景接入。
"""
from __future__ import annotations

from backend.asr.cloud_paraformer import ParaformerRealtime


class CloudWakeDetector:
    def __init__(self, keyword: str = "小二") -> None:
        self._keyword = keyword
        self._hit = False
        self._asr = ParaformerRealtime(
            self._on_result,
            model="fun-asr-flash-8k-realtime",  # 方言（重庆/四川/粤语）
            sample_rate=8000,
        )
        self._asr.start()

    def _on_result(self, is_final: bool, text: str) -> None:
        if self._keyword in (text or ""):
            self._hit = True

    def feed(self, pcm: bytes) -> bool:
        self._asr.feed(pcm)
        if self._hit:
            self._hit = False
            return True
        return False

    def close(self) -> None:
        try:
            self._asr.stop()
        except Exception:
            pass
