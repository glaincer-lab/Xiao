"""本地 ASR：FunASR（阿里开源，中文识别最强开源方案之一）。

MVP 采用整句批量识别（说完后一次出结果）；如需本地流式，
后续可启用 FunASR 的 stream 模式（AutoModel(..., stream=True) + cache）。
"""
from __future__ import annotations

import numpy as np

from backend.asr.base import ASREngine


class FunASRLocal(ASREngine):
    def __init__(self, on_result, model: str = "paraformer-zh", model_dir: str = "") -> None:
        super().__init__(on_result)
        # 本地目录优先，回退模型名（FunASR 的 model 参数支持本地路径，否则按模型名自动下载）
        self._model_path = model_dir or model
        self._model = None
        self._audio: list[np.ndarray] = []

    def start(self) -> None:
        if self._model is None:
            from funasr import AutoModel  # 延迟导入（torch 较重）

            self._model = AutoModel(
                model=self._model_path,
                vad_model="fsmn-vad",
                punc_model="ct-punc",
                disable_update=True,
            )
        self._audio = []

    def feed(self, pcm: bytes) -> None:
        self._audio.append(np.frombuffer(pcm, dtype=np.int16))

    def stop(self) -> str:
        if not self._audio:
            return ""
        audio = np.concatenate(self._audio).astype(np.float32) / 32768.0
        self._audio = []
        try:
            res = self._model.generate(input=audio, language="zh", use_itn=True)
        except Exception:
            return ""
        text = ""
        if isinstance(res, list) and res and isinstance(res[0], dict):
            text = str(res[0].get("text", "")).strip()
        if text:
            self.on_result(True, text)
        return text
