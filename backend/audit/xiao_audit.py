"""auditor（可审计回放的订阅方）：订阅 bridge 的 event_sink，追加式记录 run facts。

设计思想受 [xiaotianfotos/homerail](https://github.com/xiaotianfotos/homerail)（MIT）启发，
自研实现：作为 bridge event_sink 的订阅者，只读追加——把桥解析好的
tool/call、tool/result、assistant/chunk、assistant/message、turn/end 五类事件
（含 run_id）逐条落进 run 级事实平面；不改桥的核心解析，不破坏既有记忆/画像。
命名全套 backend/audit/ + xiao_ 前缀，不与 HomeRail 代码混淆。

接入方式（由装配层 main.py 注入 bridge 的 event_sink）：
    auditor.handle_event(kind, payload)   # kind ∈ 五类原始事件（忽略 work_step/dsh_chunk 派生态）
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Any

from backend.audit.xiao_fact_plane import XiaoFactPlane
from backend.audit.xiao_replay import XiaoReplay
from backend.audit.xiao_scorecard import XiaoScorecard

log = logging.getLogger(__name__)

# bridge 已解析并经 event_sink 上抛的原始事件类型；派生态 work_step/dsh_chunk 不入事实平面。
FACT_EVENT_TYPES: frozenset[str] = frozenset({
    "tool/call",
    "tool/result",
    "assistant/chunk",
    "assistant/message",
    "turn/end",
})


class XiaoAuditor:
    """把 bridge 的 event_sink 事件追加式写入 run 级事实平面，并提供 replay/scorecard。"""

    def __init__(
        self,
        plane: XiaoFactPlane | None = None,
        base_dir: str | None = None,
    ) -> None:
        self._plane = plane if plane is not None else XiaoFactPlane(base_dir=base_dir)
        self._lock = threading.Lock()
        # R1 限频缓冲（针对高频 assistant/chunk 流式文本）：攒到阈值或 turn/end 才批量写盘，
        # 显著降低逐条 open 写盘对磁盘寿命的损耗。低频事件（tool/call/result/message）即时落盘。
        self._chunk_buffer: list[tuple[str, dict[str, Any]]] = []  # (run_id, payload)
        self._chunk_bytes: int = 0
        self._chunk_flush_bytes: int = 4096

    # ---- 本地脱敏（R2）----
    _SENSITIVE_RX_CACHE: dict | None = None

    @classmethod
    def _sanitize_text(cls, text: str) -> str:
        """审计落盘前本地脱敏，防隐私明文裸奔（本地、不出网；R2）。

        两道措施：
        1) 复用 M0.2 网关 guard_outbound 的本地混淆（对 obfuscation_mapping 配置的实体做占位，
           当前默认映射为空时有效；未来启用映射即自动生效）。
        2) 本地黑词遮蔽兜底：对 compliance.local_only_keywords（密码/密钥/身份证/自伤类）命中片段
           统一替换为 [REDACTED]，确保即便网关未映射、敏感词也绝不明文落盘。
        网关/配置不可用时保守返回遮蔽后的文本（审计不阻塞）。
        """
        if not text or not isinstance(text, str):
            return text
        result = str(text)
        # 1) 网关本地混淆（配置了映射才替换，空映射时原样）。
        try:
            from backend.gateway.gateway import guard_outbound

            _, processed = guard_outbound(result, session_id="audit")
            if isinstance(processed, str) and processed:
                result = processed
        except Exception:  # noqa: BLE001
            pass  # 网关不可用不阻断审计（本地记录，靠措施 2 兜底）。
        # 2) 本地黑词遮蔽兜底（负则替换，绝不依赖网关映射覆盖）。
        try:
            from backend.gateway.load_config import load_compliance

            c = load_compliance()
            gw_section = c.get("compliance_gateway", c)
            sensitive = gw_section.get("local_only_keywords", []) or []
            if sensitive:
                # 敏感词及其后随的「值」（直到中文/英文标点或行尾）整体遮蔽，
                # 避免「密码 abc123」只遮蔽词、值明文残留。
                joined = "|".join(re.escape(str(s).strip()) for s in sensitive if str(s).strip())
                if joined:
                    result = re.sub(
                        r"(" + joined + r")[^，。；！？,.;!?\n]*",
                        "[REDACTED]",
                        result,
                    )
        except Exception:  # noqa: BLE001
            pass  # 配置缺失则仅靠网关（若有）；仍不阻断审计。
        return result

    @classmethod
    def _sanitize_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        """递归脱敏 payload 中的文本字段（按字段名限定，避免误伤结构化数据）。"""
        if not isinstance(payload, dict):
            return payload
        out: dict[str, Any] = {}
        for k, v in payload.items():
            if isinstance(v, str):
                # 仅对文本内容字段做混淆（人名/关系占位）；id/seq/kind 等结构字段不动。
                if k in ("text", "message", "input", "content", "output", "reason", "result", "error", "summary", "prompt"):
                    out[k] = cls._sanitize_text(v)
                else:
                    out[k] = v
            elif isinstance(v, dict):
                out[k] = cls._sanitize_payload(v)
            elif isinstance(v, list):
                out[k] = [cls._sanitize_payload(x) if isinstance(x, dict) else (
                    cls._sanitize_text(x) if isinstance(x, str) else x) for x in v]
            else:
                out[k] = v
        return out

    def flush(self) -> None:
        """强制把 chunk 缓冲刷入事实平面（turn/end 或显式调用时；R1）。"""
        with self._lock:
            self._flush_chunks()

    @property
    def plane(self) -> XiaoFactPlane:
        return self._plane

    # ---- 订阅点：bridge event_sink 逐条回调 ----
    def handle_event(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        """接收 bridge 的一条事件；只记录五类原始事件，忽略派生态，静默防抖。

        payload 需携带 run_id（由 bridge 在 _emit 时注入），否则无法归属 run，丢弃。
        """
        if kind not in FACT_EVENT_TYPES:  # 只记录五类原始事件；work_step/dsh_chunk 派生态自动忽略
            return
        data = payload or {}
        run_id = data.get("run_id")
        if not run_id:
            return
        # R2：落盘前本地脱敏（人名/关系→占位符），防隐私明文裸奔。
        safe_data = self._sanitize_payload(data)
        try:
            with self._lock:
                if kind == "assistant/chunk":
                    # R1：高频 chunk 先入缓冲，达阈值时批量落盘（保序，降低 open 次数）。
                    self._chunk_buffer.append((str(run_id), safe_data))
                    self._chunk_bytes += len(" ".join(str(x) for x in safe_data.values()))
                    if self._chunk_bytes >= self._chunk_flush_bytes:
                        self._flush_chunks()
                else:
                    # 非 chunk 事件（tool/call/result/message/turn/end）即时落盘；
                    # 但须先冲刷已缓冲的 chunk，维持事件到达顺序（否则 chunk 会乱序落尾）。
                    self._flush_chunks()
                    self._plane.append(str(run_id), kind, safe_data)
        except Exception as exc:  # noqa: BLE001
            # 只读追加故障绝不让上游事件流中断（与 bridge._emit 的静默策略一致）。
            log.warning("audit append failed event=%s run=%s err=%s", kind, run_id, exc)

    def _flush_chunks(self) -> None:
        """把 chunk 缓冲批量刷入事实平面（调用方须已持有 self._lock；R1）。"""
        if not self._chunk_buffer:
            return
        buffered, self._chunk_buffer = self._chunk_buffer, []
        # 缓冲按 (run_id, payload) 追加为独立 fact，replay 粒度不变（每 chunk 一条），只是写盘时机被推迟。
        for rid, payload_item in buffered:
            try:
                self._plane.append(rid, "assistant/chunk", payload_item)
            except Exception as exc:  # noqa: BLE001
                log.warning("audit chunk flush failed run=%s err=%s", rid, exc)
        self._chunk_bytes = 0

    # ---- 便捷查询 ----
    def replay(self, run_id: str) -> list[dict[str, Any]]:
        """按 run_id 重放生成时间线。"""
        return XiaoReplay(self._plane).replay(run_id)

    def render(self, run_id: str) -> str:
        """渲染时间线为人类可读文本。"""
        return XiaoReplay(self._plane).render(run_id)

    def scorecard(self, run_id: str) -> dict[str, Any]:
        """按 run_id 对 tool/result 做质量打点。"""
        return XiaoScorecard(self._plane).score(run_id)

    def runs(self) -> list[str]:
        return self._plane.runs()

    def close(self) -> None:
        self._plane.close()


def build_auditor(
    *,
    base_dir: str | None = None,
    plane: XiaoFactPlane | None = None,
) -> XiaoAuditor:
    """构建 auditor 的工厂函数（装配层用）。"""
    return XiaoAuditor(plane=plane, base_dir=base_dir)
