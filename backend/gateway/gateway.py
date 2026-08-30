# -*- coding: utf-8 -*-
"""出网安全网关 · 网关门面（M0 / A5，把 A1-A4 + 语义消歧串成对外网关）。

对外三个入口（写死签名，见 _M0-tasks/A5-gateway-orchestration.md）：
    guard_outbound(text, session_id) -> ("blocked"|"cloud_safe", processed)
    guard_inbound(returned_text, session_id) -> str
    get_session_context(session_id) -> SessionContext

编排流程（必须接入 A2+ 语义消歧）：
    hit = detect_blocked(text, keywords)
    未命中 -> obfuscate+register -> ("cloud_safe", processed)
    命中   -> is_semantic_available?
              ├─ 否 -> allow_words 规则（命中地在豁免词内 -> 放行，否则 blocked）
              └─ 是 -> judge_context(text, hit)
                        ├─ "safe"   -> obfuscate+register -> ("cloud_safe", processed)
                        ├─ "block"  -> ("blocked", 原文，本机留 / 静默拦截)
                        └─ "unknown"-> 回退 allow_words 规则

安全要点（AGENTS.md + M0-core §4.3）：
- **自伤红线恒不放行**：凡文本含「自杀 / 不想活了」，无论语义/规则判定结果如何，一律 blocked；
- **语义消歧是「高置信技术语境才放行」**：任何不确定（unknown）一律回退 allow_words 规则
  或本机留（fail-closed），绝不裸奔出网；
- **allow_words 缺省用语义锚点**（semantic_filter.DEFAULT_ANCHORS）作为技术语境豁免表，
  模型缺失时照样能放行「密码学 / 密钥管理」这类正常词；
- **会话级懒加载缓存**：_SESSION_CONTEXTS 仅缓存「每个 session 一个 SessionContext」，
  **占位符清单在实例内部**，绝不使用模块级全局可变 dict 当清单（防流式/多任务竞态）；
- 报错走「人话 + 下一步建议」：load_compliance 失败统一转 GatewayConfigError。

仅标准库（secrets）；MIT 自写代码，不复制 GPL/AGPL。
"""

from __future__ import annotations

import secrets
import threading

from backend.gateway.blocklist import detect_blocked
from backend.gateway.constants import SELF_HARM_KEYWORDS
from backend.gateway.load_config import load_compliance
from backend.gateway.obfuscate import obfuscate, restore
from backend.gateway.semantic_filter import (
    DEFAULT_ANCHORS,
    is_semantic_available,
    judge_context,
)
from backend.gateway.session_manifest import SessionContext

# 会话上下文缓存：key=session_id。仅缓存实例，清单在实例内部（非全局清单，勿改成全局清单）。
# 加锁保证首次创建原子；有界容量防长跑服务内存膨胀。
_SESSION_CONTEXTS: dict[str, SessionContext] = {}
_SESSIONS_LOCK = threading.Lock()
_MAX_SESSIONS = 1024

# 配置路径（默认 None = 读自带 compliance.yaml；单测可临时指向缺文件路径）。
_CONFIG_PATH: str | None = None


class GatewayConfigError(RuntimeError):
    """网关配置缺失或非法的统一异常；message 为可直接展示的中文人话。"""


def _load_compliance() -> dict:
    """加载并校验合规配置；失败统一转为「人话」错误（原因 + 下一步建议）。"""
    try:
        return load_compliance(_CONFIG_PATH)
    except Exception as exc:
        raise GatewayConfigError(
            "\n".join([
                f"出网网关无法加载合规配置：{exc}",
                "请检查 compliance.yaml 是否存在、是否为合法 YAML、契约字段是否齐全。若文件丢失，请从项目备份恢复，或回写默认配置示例。",
            ])
        ) from exc


def _contains_self_harm(text: str) -> bool:
    """含自伤红线词则返回 True（任何语境不放行出网）。"""
    return any(word in text for word in SELF_HARM_KEYWORDS)


def _allow_words(gw: dict) -> list[str]:
    """取技术语境豁免表：配置显式提供则用，否则回退语义锚点（缺模型也能放行正常词）。"""
    explicit = gw.get("allow_words")
    if isinstance(explicit, list) and explicit:
        return explicit
    return list(DEFAULT_ANCHORS)


def _rule_gate(text: str, keywords: list, allow_words: list, mapping: dict, ctx: SessionContext):
    """allow_words 规则兜底：命中位置若被豁免词覆盖 -> 放行（混淆+登记），否则本机留。"""
    if detect_blocked(text, keywords, allow_words) is None:
        processed = obfuscate(text, mapping)
        ctx.register(text, mapping, obfuscated=processed)
        return ("cloud_safe", processed)
    return ("blocked", text)


def _new_salt_nonce(session_id: str) -> str:
    """为会话生成一次性 salt_nonce，区分同 session 多次出网。"""
    return f"{session_id}:{secrets.token_hex(8)}"


def _get_or_create_context(session_id: str) -> SessionContext:
    with _SESSIONS_LOCK:
        ctx = _SESSION_CONTEXTS.get(session_id)
        if ctx is None:
            if len(_SESSION_CONTEXTS) >= _MAX_SESSIONS:
                # 有界淘汰最早生成的会话（dict 为插入序，弹出首键），防长跑内存膨胀。
                _SESSION_CONTEXTS.pop(next(iter(_SESSION_CONTEXTS)))
            ctx = SessionContext(session_id, _new_salt_nonce(session_id))
            _SESSION_CONTEXTS[session_id] = ctx
        return ctx


# 【已冻结 · T0/S4】出网安全网关三入口之一：会话上下文获取。契约见 compliance.yaml；
# 接口签名与行为已锁定（S4 生命线），禁止改动签名/返回语义。
def get_session_context(session_id: str) -> SessionContext:
    """会话级懒加载：每个 session 一次，返回同一个 SessionContext（勿共享单例）。"""
    return _get_or_create_context(session_id)


# 【已冻结 · T0/S4】出网安全网关三入口之一：出网拦截+混淆编排。契约见 compliance.yaml；
# 返回 (处置,文本) 语义已锁定，禁止改动签名/返回语义。
def guard_outbound(text: str, session_id: str) -> tuple[str, str]:
    """出网拦截 + 混淆编排。返回 (处置, 处理后的文本)。

    - "blocked"   : 文本本机留（黑词命中且非技术语境、或为自伤红线）；processed 为原文。
    - "cloud_safe": 可出网；processed 为已混淆的文本。
    """
    cfg = _load_compliance()
    gw = cfg.get("compliance_gateway", {}) or {}

    # 自伤红线硬闸：不可配置、不可放行（即便总开关关闭也要兜住，H2）。
    if _contains_self_harm(text):
        return ("blocked", text)

    if not gw.get("enabled", True):
        # 网关总开关关闭：直通（排障用途），不拦截不混淆。
        return ("cloud_safe", text)

    keywords = gw.get("local_only_keywords", []) or []
    mapping = gw.get("obfuscation_mapping", {}) or {}
    allow_words = _allow_words(gw)
    ctx = _get_or_create_context(session_id)
    ctx.open_send()  # 开启本轮出网（多轮隔离，M1）

    hit = detect_blocked(text, keywords)
    if hit is None:
        processed = obfuscate(text, mapping)
        ctx.register(text, mapping, obfuscated=processed)  # 复用已混淆文本，避免二次混淆（L4）
        return ("cloud_safe", processed)

    # 命中黑词：接入语义消歧（高置信技术语境才放行，不确定一律回退规则）。
    if is_semantic_available(cfg):
        verdict = judge_context(text, hit, cfg)
        if verdict == "safe":
            processed = obfuscate(text, mapping)
            ctx.register(text, mapping, obfuscated=processed)
            return ("cloud_safe", processed)
        if verdict == "block":
            return ("blocked", text)
        # unknown -> 回退 allow_words 规则
        return _rule_gate(text, keywords, allow_words, mapping, ctx)

    # 语义不可用：直接回退 allow_words 规则。
    return _rule_gate(text, keywords, allow_words, mapping, ctx)


# 【已冻结 · T0/S4】出网安全网关三入口之一：回程还原+校验。契约见 compliance.yaml；
# 接口签名与行为已锁定，禁止改动。
def guard_inbound(returned_text: str, session_id: str) -> str:
    """回程还原 + 校验。返回尽力还原后的文本。

    校验失败时已由 SessionContext.log_mismatch 写结构化日志（不静默），
    并按降级策略返回还原文本（best-effort：缺失/被改写的占位符保持原样）。
    """
    cfg = _load_compliance()
    gw = cfg.get("compliance_gateway", {}) or {}
    mapping = gw.get("obfuscation_mapping", {}) or {}
    ctx = _get_or_create_context(session_id)
    ctx.verify(returned_text, mapping)  # 失败会自动 log_mismatch 留痕
    return restore(returned_text, mapping)


def reset_sessions() -> None:
    """清空会话上下文缓存（测试隔离用；生产一般无需调用）。"""
    _SESSION_CONTEXTS.clear()


__all__ = [
    "guard_outbound",
    "guard_inbound",
    "get_session_context",
    "reset_sessions",
    "GatewayConfigError",
    "SELF_HARM_KEYWORDS",
]
