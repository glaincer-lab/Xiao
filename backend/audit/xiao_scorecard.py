"""scorecard（质量打点）：对某个 run 的 tool/result 做可审计的质量评分。

设计思想受 [xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）启发，
自研实现：从 append-only fact plane 读取该 run 的事实，聚焦 tool/result 打点——
统计工具调用总数/成功/失败、错误率、按工具名聚合、助手输出量，并合成 0-100 质量分。
命名全套 backend/audit/ + xiao_ 前缀，不与 HomeRail 代码混淆。

评分口径（仅评估 tool/result 质量，权重可调）：
    quality_score = clamp(100 - 错误率惩罚 - 空答惩罚 - 异常结束惩罚)
        - 错误率惩罚 = 工具结果错误率 * 60
        - 空答惩罚   = 无任何助手文本时 -25
        - 异常结束   = turn/end 非 completed 时 -10
"""
from __future__ import annotations

from typing import Any

from backend.audit.xiao_fact_plane import XiaoFactPlane
from backend.audit.xiao_replay import _text_of


class XiaoScorecard:
    """对某个 run 的事实做质量打点（聚焦 tool/result）。"""

    def __init__(self, plane: XiaoFactPlane) -> None:
        self._plane = plane

    def score(self, run_id: str) -> dict[str, Any]:
        """计算该 run 的得分卡（可 JSON 序列化）。"""
        facts = self._plane.facts(run_id)
        tool_calls = [f for f in facts if f.event == "tool/call"]
        tool_results = [f for f in facts if f.event == "tool/result"]
        chunks = [f for f in facts if f.event == "assistant/chunk"]
        msgs = [f for f in facts if f.event == "assistant/message"]
        turns = [f for f in facts if f.event == "turn/end"]

        tools_ok = 0
        tools_error = 0
        tools_by_name: dict[str, int] = {}
        for f in tool_results:
            p = f.payload
            msg = p.get("message") if isinstance(p.get("message"), dict) else p
            name = msg.get("name") or p.get("name") or "tool"
            is_err = bool(msg.get("isError") or p.get("isError"))
            if is_err:
                tools_error += 1
            else:
                tools_ok += 1
            tools_by_name[name] = tools_by_name.get(name, 0) + 1

        total_tools = len(tool_calls)
        error_rate = (tools_error / total_tools) if total_tools else 0.0

        # 助手输出量 = chunk 累计 + message 累计
        assistant_chars = sum(len(_text_of(f.payload)) for f in chunks)
        assistant_chars += sum(len(_text_of(f.payload)) for f in msgs)

        # turn/end 结束原因（判定异常结束）
        end_reason = ""
        if turns:
            last = turns[-1]
            reason = last.payload.get("reason")
            if isinstance(reason, dict):
                reason = reason.get("kind") or reason.get("reason") or reason
            end_reason = str(reason or "").strip().lower()

        quality = 100.0
        quality -= error_rate * 60.0
        if total_tools == 0 or assistant_chars == 0:
            quality -= 25.0
        if turn_end_ok(end_reason):
            pass
        else:
            quality -= 10.0
        quality = round(max(0.0, min(100.0, quality)), 1)

        return {
            "run_id": run_id,
            "tools_called": total_tools,
            "tools_results": len(tool_results),
            "tools_ok": tools_ok,
            "tools_error": tools_error,
            "tool_error_rate": round(error_rate, 4),
            "tools_by_name": tools_by_name,
            "chunk_count": len(chunks),
            "message_count": len(msgs),
            "assistant_chars": assistant_chars,
            "turn_end_count": len(turns),
            "end_reason": end_reason,
            "quality_score": quality,
        }

    def save(self, run_id: str, path: Any = None) -> dict[str, Any]:
        """打分并落盘到该 run 目录（可选，便于离线复核）。返回得分卡。"""
        from pathlib import Path
        import json

        card = self.score(run_id)
        if path is None:
            path = self._plane.run_dir(run_id) / "scorecard.json"
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False, indent=2)
        return card


def turn_end_ok(reason: str) -> bool:
    """turn/end 是否正常结束（completed）。"""
    if not reason:
        return False
    return reason in ("completed", "done", "ok", "finished")
