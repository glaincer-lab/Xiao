"""replay（可审计回放）：按 run_id 重放事实平面，生成前后因果时间线。

设计思想受 [xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）启发，
自研实现：把 append-only fact plane 的原始事实按 seq 重放，把流式 assistant/chunk
合并为完整助手消息，输出结构化的时间线条目与人类可读的渲染文本。
命名全套 backend/audit/ + xiao_ 前缀，不与 HomeRail 代码混淆。
"""
from __future__ import annotations

from typing import Any

from backend.audit.xiao_fact_plane import XiaoFact, XiaoFactPlane


def _text_of(payload: dict[str, Any]) -> str:
    """从事件载荷里稳妥抽取文本（与 bridge._data_text 同思路，纯读复用不侵入桥）。"""
    for key in ("text", "delta", "content", "message"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            t = v.get("text") or v.get("content")
            if isinstance(t, str) and t:
                return t
    return ""


def _fmt_args(args: Any) -> str:
    if isinstance(args, dict):
        try:
            s = " ".join(f"{k}={v}" for k, v in args.items())
        except Exception:
            s = str(args)
    elif isinstance(args, str):
        s = args
    else:
        s = ""
    s = " ".join(str(s).split())
    return s[:90] + ("…" if len(s) > 90 else "")


class XiaoReplay:
    """把某 run 的事实重放为有序时间线（streaming chunks 合并为助手消息）。"""

    def __init__(self, plane: XiaoFactPlane) -> None:
        self._plane = plane

    def timeline(self, run_id: str) -> list[dict[str, Any]]:
        """返回该 run 的有序时间线条目列表。

        每条形如 {"seq", "ts", "event", "text", "data"}；连续 assistant/chunk 合并成单条
        assistant/message 文本，便于按「工具调用 + 助手回答」方式阅读整轮。
        """
        events: list[dict[str, Any]] = []
        assistant_buf: list[str] = []

        def _flush_assistant() -> None:
            if assistant_buf:
                merged = "".join(assistant_buf)
                events.append(
                    {
                        "seq": _assistant_seq,
                        "ts": _assistant_ts,
                        "event": "assistant/message",
                        "text": merged,
                        "data": {"text": merged},
                    }
                )
                assistant_buf.clear()

        _assistant_seq = 0
        _assistant_ts = 0.0
        for fact in self._plane.facts(run_id):
            if fact.event == "assistant/chunk":
                if assistant_buf:
                    # 续接前一段 chunk
                    pass
                else:
                    _assistant_seq = fact.seq
                    _assistant_ts = fact.ts
                piece = _text_of(fact.payload)
                if piece:
                    assistant_buf.append(piece)
                continue
            _flush_assistant()
            events.append(self._entry(fact))
        _flush_assistant()
        return events

    def _entry(self, fact: XiaoFact) -> dict[str, Any]:
        """把单条非 chunk 事实翻译成时间线条目。"""
        p = fact.payload
        text = ""
        if fact.event == "tool/call":
            name = p.get("name") or "tool"
            text = f"tool/call {name} args={_fmt_args(p.get('arguments'))}"
        elif fact.event == "tool/result":
            msg = p.get("message") if isinstance(p.get("message"), dict) else p
            name = msg.get("name") or p.get("name") or "tool"
            is_err = bool(msg.get("isError") or p.get("isError"))
            content = msg.get("content") or p.get("content") or ""
            text = f"tool/result {name} -> {'error' if is_err else 'ok'} {str(content)[:120]}"
        elif fact.event == "turn/end":
            reason = p.get("reason")
            if isinstance(reason, dict):
                reason = reason.get("kind") or reason.get("reason") or reason
            text = f"turn/end {reason}"
        elif fact.event == "assistant/message":
            # 与流式 chunk 合并后的段落一致：直接给出消息文本
            text = _text_of(p)
        else:
            text = f"{fact.event} {_text_of(p)}"
        return {
            "seq": fact.seq,
            "ts": fact.ts,
            "event": fact.event,
            "text": text,
            "data": dict(p),
        }

    def replay(self, run_id: str) -> list[dict[str, Any]]:
        """replay 主入口：重放 run 生成时间线（字典列表，可序列化/展示）。"""
        return self.timeline(run_id)

    def render(self, run_id: str) -> str:
        """把时间线渲染成人类可读文本（用于复盘/日志查看）。"""
        lines = []
        for item in self.timeline(run_id):
            lines.append(f"[{item['seq']:>3}] {item['text']}")
        return "\n".join(lines)
