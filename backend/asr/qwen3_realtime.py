"""云端流式 ASR：阿里云百炼 qwen3-asr-flash-realtime（OmniRealtime 接口）。

与 cloud_paraformer.py 的 Recognition（dashscope.audio.asr.Recognition）不同，
本引擎走 dashscope.audio.qwen_omni.OmniRealtimeConversation 的 WebSocket 全双工协议，
纯文本输出（output_modalities=[TEXT]），用服务端 VAD 断句。

依赖：dashscope >= 1.27（含 dashscope.audio.qwen_omni 模块）。
参考：阿里云官方「Qwen 3 ASR SDK」示例（OmniRealtimeConversation + TranscriptionParams）。
"""
from __future__ import annotations

import base64

from backend.asr.base import ASREngine


class Qwen3ASRRealtime(ASREngine):
    """qwen3-asr-flash-realtime 云端流式识别引擎。

    注意：服务端默认开启 VAD（turn_detection=server_vad），会自行断句；
    与客户端 Silero VAD 的配合方式需实测确认。本类按官方示例实现，未在本机验证。
    """

    def __init__(self, on_result, api_key: str | None = None, model: str = "qwen3-asr-flash-realtime") -> None:
        super().__init__(on_result)
        self._api_key = api_key
        self._model = model
        self._conversation = None
        self._final_text = ""

    def _make_callback(self):
        outer = self

        class _Callback:
            def on_open(self) -> None:
                pass

            def on_close(self, close_status_code, close_msg) -> None:
                pass

            def on_event(self, response) -> None:
                # _on_message 会把 websocket 消息 json.loads 后传入（dict）
                if not isinstance(response, dict):
                    return
                t = response.get("type", "")
                if t == "conversation.item.input_audio_transcription.text":
                    # 增量结果：text 为当前片段，stash 为尚未固定的暂存
                    text = (response.get("text") or "") + (response.get("stash") or "")
                    if text:
                        outer.on_result(False, text)
                elif t == "conversation.item.input_audio_transcription.completed":
                    # 一句话结束的最终文本
                    text = response.get("transcript") or ""
                    if text:
                        outer._final_text += text
                        outer.on_result(True, outer._final_text)

        return _Callback()

    def start(self) -> None:
        from dashscope.audio.qwen_omni import MultiModality, OmniRealtimeConversation
        from dashscope.audio.qwen_omni.omni_realtime import TranscriptionParams

        self._final_text = ""
        self._conversation = OmniRealtimeConversation(
            model=self._model,
            callback=self._make_callback(),
            api_key=self._api_key,
        )
        self._conversation.connect()
        self._conversation.update_session(
            output_modalities=[MultiModality.TEXT],
            enable_input_audio_transcription=True,
            transcription_params=TranscriptionParams(
                language="zh",
                sample_rate=16000,
                input_audio_format="pcm",
            ),
        )

    def feed(self, pcm: bytes) -> None:
        if self._conversation is None:
            return
        self._conversation.append_audio(base64.b64encode(pcm).decode("ascii"))

    def stop(self) -> str:
        conv = self._conversation
        self._conversation = None
        if conv is None:
            return self._final_text
        try:
            conv.end_session(timeout=10)
        except Exception:
            pass
        try:
            conv.close()
        except Exception:
            pass
        return self._final_text

    def close(self) -> None:
        if self._conversation is not None:
            try:
                self._conversation.close()
            except Exception:
                pass
            self._conversation = None
