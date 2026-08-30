# -*- coding: utf-8 -*-
"""出网安全网关 · 会话级占位符清单 + 回程还原校验（M0 / A4）。

为流式/多任务并发出网提供「会话级、防竞态」的占位符清单与回程校验，
彻底替代全局单例（旧版把清单放在模块级 dict，网络序异步交错会高频误报降级）。

设计要点（第三使用者视角 / AGENTS.md + M0-core §4.3 v4.1.1）：
- **每个 session 一个 SessionContext 实例**：占位符清单（manifest）挂在实例上，
  **绝不使用模块级全局可变 dict 当清单**，从而天然隔离并发会话，杜绝竞态；
- **salt_nonce 区分同 session 多次出网**：manifest 按 salt_nonce 分桶登记，
  同一会话内多次出网各自记账、互不混淆；
- **verify 失败必 log_mismatch 留痕**：结构化日志（JSON 一行），含会话号、salt_nonce、
  期望占位符、缺失占位符、返回文本、还原结果与时间戳，绝不静默；
- 依赖 A3 的 obfuscate/restore（占位替代引擎）；A2 的 detect_blocked 由 A5 编排层
  在调用本模块**之前**完成黑词拦截，因此本模块保持纯清单/校验职责，不与黑词耦合；
- obfuscate_fn / restore_fn / logger 允许注入（签名一致），便于单测假实现与日志捕获。

仅标准库（json / logging / datetime）；MIT 自写代码，不复制 GPL/AGPL。
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from datetime import datetime, timezone

from backend.gateway.load_config import load_compliance
from backend.gateway.obfuscate import matched_keys, obfuscate, restore


_LOGGER = logging.getLogger("backend.gateway.session_manifest")

# 结构化日志事件名（见 EVENT_REGISTRY：gateway.* 事件）
_EVENT_RESTORE_MISMATCH = "gateway.restore_mismatch"


def _redact(value: str, limit: int = 120) -> str:
    """日志里截断超长文本，避免一次还原失败把整段响应泼进日志。"""
    if value is None:
        return ""
    return value if len(value) <= limit else value[:limit] + "…(截断)"


class SessionContext:
    """一个出网会话的占位符清单与还原校验上下文。

    每个（session_id, salt_nonce）对应一次独立出网/回程上下文；建议由
    A5 的 get_session_context(session_id) 懒加载并复用，勿自行共享实例。
    """

    def __init__(
        self,
        session_id: str,
        salt_nonce: str,
        *,
        obfuscate_fn=obfuscate,
        restore_fn=restore,
        logger: logging.Logger | None = None,
    ) -> None:
        self.session_id = session_id
        self.salt_nonce = salt_nonce
        self._obfuscate = obfuscate_fn
        self._restore = restore_fn
        self._logger = logger or _LOGGER
        # manifest：{salt_nonce: set(占位符)}。每个 salt 分桶，避免同 session 多次出网混账。
        self._manifest: dict[str, set[str]] = {}
        # 最近一次出网混淆后的文本（供 A5/日志观测，可读但不参与 manifest）。
        self._last_obfuscated: str = ""
        # 结构化 mismatch 留痕（列表），供调用方/单测检查，不静默。
        self._mismatch_log: list[dict] = []
        # 当前轮次的 salt_nonce（A5 每次出网前 open_send 切换；缺省回退到构造时的 salt_nonce）。
        self._active: str | None = None
        # 会话内清单读写的轻量锁：流式/多任务并发 register/verify 不交错。
        self._lock = threading.RLock()

    # ---- 清单 ---------------------------------------------------------
    def _all_placeholders(self) -> set[str]:
        """取本会话所有 salt 桶里的占位符并集（回程校验基准）。"""
        out: set[str] = set()
        for bucket in self._manifest.values():
            out |= bucket
        return out

    def _current_key(self) -> str:
        """本轮校验桶的 salt：优先 open_send 设置的当前轮，否则回退构造时的 salt_nonce。"""
        return self._active if self._active is not None else self.salt_nonce

    def _current_placeholders(self) -> set[str]:
        """取本轮（当前 salt 桶）登记的占位符集合。"""
        return set(self._manifest.get(self._current_key(), set()))

    def open_send(self, salt_nonce: str | None = None) -> str:
        """开启新一轮出网：切换当前校验桶（区分同 session 多次出网）。

        A5 每次调用 guard_outbound 前调用；无参时自动生成新 nonce。
        返回本轮 salt；若传入 salt_nonce 则复用（便于测试/回程对应）。
        """
        with self._lock:
            self._active = salt_nonce or secrets.token_hex(8)
            self._manifest.setdefault(self._active, set())
        return self._active

    def register(self, text: str, mapping: dict[str, str], obfuscated: str | None = None) -> int:
        """出网前混淆并记录占位符清单，返回本次实际替换的占位符数量。

        text:    出网前原文。
        mapping: {真名: 占位符}（与 A3 obfuscate 一致）。

        返回：本次调用实际引入的占位符个数（0 表示文本无登记实体或 mapping 为空）。
        只会登记**真正被替换**的占位符（A3 matched_keys 精确到最长优先），
        从而在「我妈」+「我妈妈」并存时，只登记被替换的那一个，不误登记。
        """
        if not text or not mapping:
            return 0
        if obfuscated is None:
            obfuscated = self._obfuscate(text, mapping)
        self._last_obfuscated = obfuscated
        used = matched_keys(text, mapping)  # 与 obfuscate 相同的最长优先匹配
        placeholders = {mapping[key] for key in used}
        if placeholders:
            with self._lock:
                self._manifest.setdefault(self._current_key(), set()).update(placeholders)
        return len(placeholders)

    def verify(self, returned_text: str, mapping: dict[str, str]) -> bool:
        """回程校验占位符是否被丢弃/改写。

        returned_text: 云端返回的**原始**文本（含占位符，未还原）。
        mapping:       出网时用的同一 mapping。

        返回：所有登记占位符都还在返回文本中 -> True；
              任一占位符被丢弃或改写（如 User_Kinship_Mother 被改成「妈妈」）-> False，
              且自动调用 log_mismatch 留痕（不静默）。
        """
        expected = self._current_placeholders()
        if not expected:
            # 本轮未登记任何占位符，无从校验，视为通过（A5 亦按空映射直通）。
            return True
        with self._lock:
            missing = [p for p in sorted(expected) if p not in returned_text]
        ok = not missing
        if not ok:
            self.log_mismatch(returned_text, mapping, missing=missing)
        return ok

    def log_mismatch(
        self, returned_text: str, mapping: dict[str, str], missing: list[str] | None = None
    ) -> None:
        """写一条结构化还原失败日志，并加入本实例留痕（不静默）。

        returned_text: 云端返回的原始文本。
        mapping:       出网时用的 mapping（用于还原、展示应还原结果）。
        missing:       缺失/被改写的占位符（可选，便于直接调用本方法时也能记录）。
        """
        if missing is None:
            missing = [p for p in sorted(self._current_placeholders()) if p not in returned_text]
        record = {
            "event": _EVENT_RESTORE_MISMATCH,
            "session_id": self.session_id,
            "salt_nonce": self._current_key(),  # 记录本轮 salt，便于定位多次出网中的哪一轮
            "expected_placeholders": sorted(self._current_placeholders()),
            "missing_placeholders": sorted(missing),
            "returned_text": _redact(returned_text),
            # 刻意不含 restored_text 与「占位符->真名」映射：日志不落真实实体，避免隐私泄露（M2）。
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        self._mismatch_log.append(record)
        self._logger.warning(json.dumps(record, ensure_ascii=False))


def load_compliance_for(path: str | None = None) -> dict:
    """复用 A1 的 load_compliance，加载出网网关合规配置。

    等价于 load_config.load_compliance(path)；为 A4/A5 提供一个统一的取配置入口，
    报错（缺文件/字段/类型）同样走「人话 + 下一步建议」的 ComplianceConfigError。
    """
    return load_compliance(path)


__all__ = ["SessionContext", "load_compliance_for"]
