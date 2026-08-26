"""唤醒词检测：Sherpa-ONNX KWS（本地中文关键词）。

替换原 openWakeWord：用 sherpa-onnx 的 zipformer 关键词检测模型，本地识别中文
唤醒词「小二」，免费、离线、无需自训练。

依赖（见 ROADMAP.md Phase 0）：
  1. pip install sherpa-onnx
  2. 下载模型 sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01 到 models/ 目录
参考：sherpa-onnx 官方 python-api-examples/keyword-spotter.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from backend.config import config


class WakeWordDetector:
    def __init__(self) -> None:
        try:
            import sherpa_onnx
        except ImportError as e:
            raise RuntimeError(
                "缺少 sherpa-onnx，请先执行：pip install sherpa-onnx，"
                "并下载 KWS 模型到 models/（见 ROADMAP.md Phase 0）"
            ) from e

        self._keyword = config.get("wake_word.keyword", "小二")
        self._pinyin = config.get("wake_word.pinyin", "x iǎo èr")  # 模型按拼音匹配，空格分隔声母/韵母
        self._sample_rate = int(config.get("audio.sample_rate", 16000))
        model_dir = Path(config.get(
            "wake_word.model_dir",
            "models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01",
        ))

        # sherpa-onnx 关键词文件：每行一个关键词，拼音按空格分隔（如 "x iǎo èr"）
        keywords_file = Path(".tmp") / "keywords.txt"
        keywords_file.parent.mkdir(parents=True, exist_ok=True)
        keywords_file.write_text(f"{self._pinyin} @{self._keyword}\n", encoding="utf-8")

        # 注意：下方 onnx 文件名是 zipformer KWS 模型的标准布局；
        # 若你下载的模型文件名不同，按实际文件名改这三行。
        self._spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(model_dir / "tokens.txt"),
            encoder=str(model_dir / "encoder-epoch-12-avg-2-chunk-16-left-64.onnx"),
            decoder=str(model_dir / "decoder-epoch-12-avg-2-chunk-16-left-64.onnx"),
            joiner=str(model_dir / "joiner-epoch-12-avg-2-chunk-16-left-64.onnx"),
            num_threads=2,
            max_active_paths=4,
            keywords_file=str(keywords_file),
            keywords_threshold=float(config.get("wake_word.threshold", 0.25)),
            provider="cpu",
        )
        self._stream = self._spotter.create_stream()

    def feed(self, pcm: bytes) -> bool:
        """喂入 PCM（int16），返回本次是否命中唤醒词。"""
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return False
        self._stream.accept_waveform(self._sample_rate, samples)
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
            # 关键：get_result 必须在循环内（每次 decode 之后）取，循环外取会被覆盖为空
            if self._spotter.get_result(self._stream):
                self._spotter.reset_stream(self._stream)
                return True
        return False


def build_wake_word():
    """按 wake_word.engine 选择唤醒引擎：sherpa（本地）/ cloud（云端方言）/ omni（一体化）。"""
    engine = config.get("wake_word.engine", "sherpa")
    keyword = config.get("wake_word.keyword", "小二")

    if engine == "cloud":
        from backend.audio.wake_cloud import CloudWakeDetector

        return CloudWakeDetector(keyword=keyword)

    if engine == "omni":
        from backend.audio.wake_omni import OmniWakeDetector

        omni_cfg = config.section("llm.omni")
        return OmniWakeDetector(
            base_url=omni_cfg.get("base_url", "http://localhost:8000/v1"),
            model=omni_cfg.get("model", "openbmb/MiniCPM-o-4_5"),
            api_key=omni_cfg.get("api_key"),
            keyword=keyword,
        )

    return WakeWordDetector()
