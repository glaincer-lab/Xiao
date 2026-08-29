"""统一报错映射（E2c）：管线任何环节的异常 → 一句人话。

规则（dev/feature-roadmap.md E2c）：401 = Key 失效；429 = 额度/限流；超时 = 网络。
原始异常永远只进后端日志（print / 前端日志面板），不进用户眼前、不抛堆栈。

两个入口：
  human_reason(e)     —— 拿到异常对象时用（openai / httpx / 超时等）；
  reason_from_text(s) —— 拿到的是错误文本时用（如后台任务落库的 str(e)）。

所有返回都是可直接语音播报、可直接放进设置页提示的中文短句。
"""

from __future__ import annotations

import asyncio

REASON_TIMEOUT = "连接超时：网络不通或服务地址填错了，请检查后重试"
REASON_CONN = "无法连接服务：请检查网络是否可用，以及服务地址（Base URL）是否填对"
REASON_KEY = "密钥无效或没有权限（401/403）：请重新粘贴完整且未过期的 API Key"
REASON_404 = "接口或模型不存在（404）：请检查服务地址与模型名是否填对"
REASON_429 = "额度不足或请求太频繁（429）：请到服务商控制台确认余额与限流，稍后再试"
REASON_400 = "请求被拒绝（400）：通常是模型名填错，请核对模型名后重试"
REASON_5XX = "服务商服务端开小差（5xx）：请稍后再试"
DEFAULT_REASON = "出了点问题，请稍后再试"


def _openai():
    """懒加载 openai：纯 L0（无云端依赖）场景下没装也不报错。"""
    try:
        import openai

        return openai
    except ImportError:
        return None


def human_reason(e: BaseException, default: str = DEFAULT_REASON) -> str:
    """异常 → 一句人话；未识别的异常原样返回 default，绝不泄漏堆栈给用户。"""
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return REASON_TIMEOUT
    oa = _openai()
    if oa is not None and isinstance(e, oa.APIConnectionError):
        return REASON_CONN
    status = getattr(e, "status_code", None)
    if status in (401, 403):
        return REASON_KEY
    if status == 404:
        return REASON_404
    if status == 429:
        return REASON_429
    if status == 400:
        return REASON_400
    if isinstance(status, int) and status >= 500:
        return REASON_5XX
    return default


_KEY_WORDS = ("401", "403", "unauthorized", "invalid api key", "invalid_api_key", "unauthenticated")
_QUOTA_WORDS = (
    "429",
    "rate limit",
    "ratelimit",
    "throttl",
    "quota",
    "insufficient",
    "arrear",
    "欠费",
    "余额不足",
    "限流",
)
_TIMEOUT_WORDS = ("timeout", "timed out", "超时")
_NOTFOUND_WORDS = ("404", "not found", "not exist", "no such")
_PATH_WORDS = ("enoent", "filenotfounderror", "找不到文件", "无法找到")
_CONN_WORDS = (
    "connection",
    "getaddrinfo",
    "unreachable",
    "refused",
    "reset by peer",
    "ssl",
)


def reason_from_text(text: str, default: str = "未知错误") -> str:
    """错误文本 → 人话（任务落库的 str(e)、子进程输出等）；识别不出给截断的原文。"""
    t = str(text or "").strip()
    if not t:
        return default
    low = t.lower()
    if any(k in low for k in _KEY_WORDS):
        return "密钥无效或没有权限：请到设置里重新粘贴完整且未过期的 API Key"
    if any(k in low for k in _QUOTA_WORDS):
        return "额度不足或请求太频繁：请到服务商控制台确认余额与限流，稍后再试"
    if any(k in low for k in _TIMEOUT_WORDS):
        return REASON_TIMEOUT
    if any(k in low for k in _NOTFOUND_WORDS):
        return "接口、模型或文件不存在：请检查服务地址、模型名或文件路径"
    if any(k in low for k in _PATH_WORDS):
        return "文件或命令不存在：请检查相关路径配置"
    if any(k in low for k in _CONN_WORDS):
        return "无法连接服务：请检查网络是否可用，以及服务地址是否填对"
    return t if len(t) <= 80 else t[:77] + "…"
