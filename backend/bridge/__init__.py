"""DSH 桥接层：语音系统唯一与 DeepSeek Harness 耦合的模块。"""
from backend.bridge.dsh_bridge import DSHBridge, DSHCancelled
from backend.bridge.dsh_web_bridge import DSHWebBridge, DSHWebRpcError, DSHWebUnavailable
from backend.config import config


def build_bridge(event_sink=None) -> DSHBridge:
    """按 bridge.mode 构建桥：web=仅流式；auto=流式优先、不可用自动回退；headless=仅一次性进程。"""
    mode = str(config.get("bridge.mode", "auto") or "auto").strip().lower()
    if mode in ("web", "auto"):
        return DSHWebBridge(event_sink=event_sink, fallback=(mode == "auto"))
    return DSHBridge()


__all__ = [
    "DSHBridge",
    "DSHCancelled",
    "DSHWebBridge",
    "DSHWebRpcError",
    "DSHWebUnavailable",
    "build_bridge",
]