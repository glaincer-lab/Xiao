"""唤醒词检测：Sherpa-ONNX KWS（本地中文关键词）。

替换原 openWakeWord：用 sherpa-onnx 的 zipformer 关键词检测模型，本地识别中文
唤醒词「小二」，免费、离线、无需自训练。

依赖（见 ROADMAP.md Phase 0）：
  1. pip install sherpa-onnx
  2. 下载模型 sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01 到 models/ 目录
参考：sherpa-onnx 官方 python-api-examples/keyword-spotter.py
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from backend.config import OMNI_BASE_URL, OMNI_MODEL, config

logger = logging.getLogger(__name__)


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
        # 优先读多方案当前项的 modelDir，回退单字段 model_dir，再回退默认
        _default_dir = "models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
        _md = ""
        _models = config.get("wake_word.models", None)
        if _models:
            _active = config.get("wake_word.active")
            _m = next((x for x in _models if x.get("id") == _active), (_models[0] if _models else None))
            if _m:
                _md = _m.get("modelDir", "") or ""
        if not _md:
            _md = config.get("wake_word.model_dir", "") or ""
        if not _md:
            _md = _default_dir
        model_dir = Path(_md)
        if not model_dir.is_absolute():
            from backend.config import ROOT

            model_dir = ROOT / model_dir

        # sherpa-onnx 关键词文件：每行一个关键词，拼音按空格分隔（如 "x iǎo èr"）
        # 基于项目根目录而非进程 CWD，保证从任意目录启动都能落地到 .tmp
        from backend.config import ROOT

        keywords_file = ROOT / ".tmp" / "keywords.txt"
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
    """按 wake_word.engine 选择唤醒引擎：sherpa（本地）/ omni（一体化）。"""
    engine = config.get("wake_word.engine", "sherpa")
    keyword = config.get("wake_word.keyword", "小二")

    if engine == "omni":
        from backend.audio.wake_chain import FallbackWakeDetector
        from backend.audio.wake_omni import OmniWakeDetector

        omni_cfg = config.section("llm.omni")
        primary = OmniWakeDetector(
            base_url=omni_cfg.get("base_url", OMNI_BASE_URL),
            model=omni_cfg.get("model", OMNI_MODEL),
            api_key=omni_cfg.get("api_key"),
            keyword=keyword,
        )
        # 本地 sherpa 唤醒保底：omni 失败/超时自动回退，保证「喊得醒」
        try:
            fallback = WakeWordDetector()
        except Exception as e:  # noqa: BLE001
            logger.warning("本地 sherpa 唤醒不可用，omni 唤醒无本地回退: %s", e)
            return primary
        return FallbackWakeDetector(primary, fallback)

    return WakeWordDetector()
