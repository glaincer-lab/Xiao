# -*- coding: utf-8 -*-
"""M1-C 记忆巩固调度器（云摘要经网关）—— 全项目唯一接触云 LLM 与出网网关的子项目。

规格来源：`docs/specs/M1-memory.md` §4.4（三源规则 + 写锁排他 + 原子提交）+ §4.1（沉淀管线）。

# 设计落点（把「出网必过网关」写进逻辑）
1. **出网必过 `guard_outbound`**：任何要送去云 LLM 的会话原文，先经
   `backend.gateway.gateway.guard_outbound(text, session_id)` 拦截与混淆；
   返回 `cloud_safe` 才允许调用云 LLM，返回 `blocked` 则本机留、绝不裸奔出网。
   云 LLM 返程再经 `guard_inbound(returned_text, session_id)` 还原（占位符回填）。
2. **写锁排他**：后台巩固线程的用户「回来」信号 = `cancel_token` 置真。
   `apply_profile` **第一件事**就是检查取消标记——已取消则立刻抛
   `ConsolidationCancelled`，**在获取写锁之前**就退出，从而绝不执行持久化写锁、
   也绝不覆盖内存/缓存。新发生的日常交互写入拥有绝对排他写权限。
3. **原子提交**：巩固分「提取候选 → 提交画像」两步。提交画像为原子操作：整体写入
   `ProfileStore`，任一异常则回滚到上一版本并标记 `consolidation_pending`，
   下次巩固优先续做而非从头。成功后再发布 `memory.profile_updated` 事件。
4. **优雅退避**：网络 I/O 中途绝不断杀线程——用户回来 → 置 `cancel_token`，
   让后台巩固**静默跑完 I/O 并丢弃返回值**（不提交、不覆盖），丢线程不丢数据。

# 测试策略（DoD）
测试一律 mock `create_client()`（云 LLM）与 `guard_outbound`（网关），**不上网**；
真实云调用需网络 + Key。`MemEntry`/`DataTrack` 可能尚未落地（`schema.py`/`datatrack.py`），
本模块**不硬 import** 它们——`data_track`/`store`/`llm_client` 均可注入，测试按
`MEMV1_CONTRACT.md` 自建最小桩。

仅标准库 + 复用现有 `backend.gateway` / `backend.llm`；MIT。
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.event_bus import bus
from backend.llm.base import ChatMessage

_LOGGER = logging.getLogger("backend.memv1.consolidate")

# 巩固调度阈值：锁屏 或 GetLastInputInfo 无键鼠 >15 分钟触发（规格 §4.4）。
# 实际「是否达到阈值」由上层调度器判断（GetLastInputInfo 为 Windows API，环境相关）；
# 本模块提供常量供调度器引用。
IDLE_CONSOLIDATION_SECONDS = 15 * 60

# 画像条目上限（MEMV1_CONTRACT §容量策略：超限低置信淘汰；此处仅做最新优先截断兜底）。
PROFILE_MAX_ENTRIES = 500

# 默认画像存储路径（相对路径，跨机器可移植；由 Environment 安装处 cwd 解析）。
_PROFILE_PATH = Path("logs/memv1_profile.json")

# 写锁：画像提交的绝对排他 + 原子提交互斥。cancelled 旧线程必须在此锁获取前就被拒绝。
_WRITE_LOCK = threading.RLock()

# 惰性单例（延迟创建，避免 import 时初始化云客户端触发配置/网络副作用）。
_llm_client: Any = None


# --------------------------------------------------------------------------- #
# 异常
# --------------------------------------------------------------------------- #
class ConsolidationError(RuntimeError):
    """巩固调度统一异常基类。"""


class ConsolidationCancelled(ConsolidationError):
    """巩固已被取消：禁止执行持久化写锁与内存缓存覆盖（写锁排他）。"""


class ConsolidationParseError(ConsolidationError):
    """云端返回的 JSON 无法解析为结构化候选。"""


class CancellationToken:
    """可复用的取消标记（写作「优雅退避」信号）。

    后台巩固线程持有一份；用户「回来」（宏观状态切回 ACTIVE）时由调度器调用
    `cancel()`，巩固线程读 `is_cancelled()` / 直接调用实例（可调用）即可感知。
    """

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def __bool__(self) -> bool:
        return self._cancelled

    def __call__(self) -> bool:
        return self._cancelled


# --------------------------------------------------------------------------- #
# 固化 Prompt（JSON Schema 写死）
# --------------------------------------------------------------------------- #
# 巩固输出 JSON Schema（写死，注入系统提示词）。字段名必须与 M1-memory.md §4.4 完全一致。
CONSOLIDATION_SCHEMA: dict[str, Any] = {
    "summary": "一句话摘要",
    "emotional_tag": "0-5",
    "suggested_entities": ["疑似新人名"],
}


def build_consolidation_system_prompt() -> str:
    """构造巩固系统提示词（含 JSON Schema + 约束指令）。

    该提示词作为 system 消息送入云 LLM；返回文本必须是符合 schema 的单个 JSON 对象。
    """
    schema_text = json.dumps(CONSOLIDATION_SCHEMA, ensure_ascii=False, indent=2)
    return (
        "你是「小二」的记忆巩固助手。请阅读会话原文，把它提炼为一条结构化记忆摘要。\n"
        "只输出一个 JSON 对象，不要包含任何其它解释或代码围栏，字段必须与下面 Schema 一致：\n"
        f"{schema_text}\n"
        "约束指令：若原文出现高频、未被混淆符替代的疑似新人名，提取入 `suggested_entities`。"
    )


# --------------------------------------------------------------------------- #
# 云 LLM 客户端工厂（复用现有 backend.llm.factory.build_llm）
# --------------------------------------------------------------------------- #
def create_client() -> Any:
    """创建 OpenAI 兼容云 LLM 客户端（复用现有 `backend.llm.factory.build_llm`）。"""
    from backend.llm.factory import build_llm

    return build_llm()


def _get_llm_client() -> Any:
    """惰性返回全局 LLM 客户端（供测试拆换）。"""
    global _llm_client
    if _llm_client is None:
        _llm_client = create_client()
    return _llm_client


# --------------------------------------------------------------------------- #
# 网关（出网必过 —— 唯一出网通道）
# --------------------------------------------------------------------------- #
def _guard_outbound(text: str, session_id: str) -> tuple[str, str]:
    """出网前拦截 + 混淆。把「出网必过网关」钉死在这里。
    返回 (处置, 处理文本)：`cloud_safe` 才允许出网；`blocked` 则本机留。
    """
    from backend.gateway import gateway

    return gateway.guard_outbound(text, session_id)


def _guard_inbound(returned_text: str, session_id: str) -> str:
    """回程还原（占位符回填）。"""
    from backend.gateway import gateway

    return gateway.guard_inbound(returned_text, session_id)


# --------------------------------------------------------------------------- #
# 画像存储（原子提交）
# --------------------------------------------------------------------------- #
class ProfileStore:
    """画像持久化：原子提交（tmp + os.replace），避免中途崩溃留下半份画像。

    结构：``{"entries": [...], "version": int, "pending": bool}``。
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else _PROFILE_PATH

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"entries": [], "version": 0, "pending": False}
        if not isinstance(data, dict):
            data = {"entries": [], "version": 0, "pending": False}
        data.setdefault("entries", [])
        data.setdefault("version", 0)
        data.setdefault("pending", False)
        return data

    def save(self, profile: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    def restore(self, snapshot: dict[str, Any]) -> None:
        """回滚到上一版本（原子存储下等价于重新保存快照）。"""
        self.save(snapshot)

    def set_pending(self, flag: bool) -> None:
        profile = self.load()
        profile["pending"] = bool(flag)
        self.save(profile)


def _default_store() -> ProfileStore:
    return ProfileStore()


def _default_datatrack() -> Any:
    """默认会话数据轨（复用 M1-A 的 backend.memv4.DataTrack）。

    惰性 import：若 M1-A 未落地则保持不硬依赖（调用方应注入 data_track）。
    """
    from backend.memv4 import DataTrack

    return DataTrack()


# --------------------------------------------------------------------------- #
# 候选解析
# --------------------------------------------------------------------------- #
def _clamp_affective(value: Any) -> int:
    """emotional_tag 夹到 0-5 整数（缺省 0）。"""
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, min(5, n))


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _entry(
    *,
    content: str,
    source: str,
    luminance: int,
    effective_at: str,
    confidence: float,
) -> dict[str, Any]:
    """按 MEMV1_CONTRACT MemEntry 五要素 schema 构造一条最小条目（不硬 import schema）。"""
    return {
        "id": _new_id(),
        "content": content,
        "scope": "global",
        "scope_detail": {},
        "effective_at": effective_at,
        "source": source,
        "status": "active",
        "confirmed": False,
        "affective_luminance": luminance,
        "confidence": confidence,
        "encrypted": False,
        "enc_token": "",
    }


def _extract_json(text: str) -> dict[str, Any]:
    """从 LLM 返回文本中稳健提取第一个 JSON 对象（容忍代码围栏与前后杂文）。"""
    payload = text or ""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", payload, re.DOTALL)
    if fenced:
        payload = fenced.group(1)
    start = payload.find("{")
    if start == -1:
        raise ConsolidationParseError("云端返回中未找到 JSON 对象")
    depth = 0
    end = -1
    for i in range(start, len(payload)):
        ch = payload[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        raise ConsolidationParseError("云端返回的 JSON 括号不匹配")
    obj_text = payload[start:end]
    try:
        obj = json.loads(obj_text)
    except json.JSONDecodeError as exc:
        raise ConsolidationParseError("云端返回的 JSON 无法解析") from exc
    if not isinstance(obj, dict):
        raise ConsolidationParseError("JSON 根节点必须是对象")
    return obj


def parse_candidates(text: str) -> list[dict[str, Any]]:
    """把云 LLM 返回（经 guard_inbound 还原后）解析为候选画像条目列表。

    产出：1 条 summary 条目 + 每个 `suggested_entities` 名各 1 条（source=inferred）。
    """
    obj = _extract_json(text)
    summary = obj.get("summary")
    if not summary or not str(summary).strip():
        raise ConsolidationParseError("缺少 summary 字段")
    emotional = _clamp_affective(obj.get("emotional_tag"))
    entities = obj.get("suggested_entities") or []
    if not isinstance(entities, list):
        entities = []
    today = datetime.now(timezone.utc).date().isoformat()

    candidates: list[dict[str, Any]] = [
        _entry(
            content=str(summary).strip(),
            source="inferred",
            luminance=emotional,
            effective_at=today,
            confidence=0.6,
        )
    ]
    for ent in entities:
        if isinstance(ent, str) and ent.strip():
            candidates.append(
                _entry(
                    content=f"疑似新人名：{ent.strip()}",
                    source="inferred",
                    luminance=emotional,
                    effective_at=today,
                    confidence=0.4,
                )
            )
    return candidates


def _join_session_logs(logs: list[dict[str, Any]]) -> str:
    """拼接会话原文行（取每条记录的 text 字段，忽略空行）。"""
    lines = []
    for item in logs:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "") or item.get("payload", "") or "")
        if text.strip():
            lines.append(text.strip())
    return "\n".join(lines)


def _merge_entries(old_entries: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合并旧画像与新增候选；按 content 去重（同内容保留后者），并截断到容量上限。"""
    merged: list[dict[str, Any]] = list(old_entries)
    merged.extend(candidates)
    seen: dict[str, dict[str, Any]] = {}
    for e in merged:
        if isinstance(e, dict):
            seen.setdefault(str(e.get("content", "")), e)
    out = list(seen.values())
    if len(out) > PROFILE_MAX_ENTRIES:
        out = out[-PROFILE_MAX_ENTRIES:]
    return out


# --------------------------------------------------------------------------- #
# 原子提交（写锁排他）
# --------------------------------------------------------------------------- #
def apply_profile(
    candidates: list[dict[str, Any]],
    store: Any | None = None,
    *,
    cancel_token: Callable[[], bool] | CancellationToken | None = None,
    last_consolidated_ts: float | None = None,
) -> None:
    """原子提交画像：全部写入或回滚到上一版本。

    - **写锁排他**：先查 `cancel_token`，已取消则立刻抛 `ConsolidationCancelled`
      （**在获取 `_WRITE_LOCK` 之前**就退出）→ 绝不执行持久化写锁、绝不覆盖画像。
    - **原子提交**：整体写入 `ProfileStore`；任一异常 → 回滚到上一版本 + 标记
      `consolidation_pending`（下次巩固优先续做），并抛 `ConsolidationError`。
    - 成功：发布 `memory.profile_updated` 事件。
    """
    if not isinstance(candidates, list):
        raise TypeError("candidates 必须为列表")

    # 写锁排他：取消标记必须在拿到写锁**之前**被拒绝。
    if cancel_token is not None and (cancel_token() if callable(cancel_token) else bool(cancel_token)):
        raise ConsolidationCancelled("巩固已取消：禁止执行持久化写锁与内存缓存覆盖")

    store = store if store is not None else _default_store()

    with _WRITE_LOCK:
        old = store.load()
        new_entries = _merge_entries(old.get("entries", []), candidates)
        new_version = int(old.get("version", 0)) + 1
        snapshot = copy.deepcopy(old)
        pending_was = bool(old.get("pending", False))
        try:
            new_profile = {
                "entries": new_entries,
                "version": new_version,
                "pending": False,
            }
            if last_consolidated_ts is not None:
                new_profile["last_consolidated_ts"] = float(last_consolidated_ts)
            store.save(new_profile)
        except Exception as exc:  # noqa: BLE001
            # 原子提交失败：回滚到上一版本 + 记 consolidation_pending。
            try:
                store.restore(snapshot)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("巩固回滚失败")
            try:
                store.set_pending(True)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("标记 consolidation_pending 失败")
            raise ConsolidationError(
                f"巩固画像原子提交失败，已回滚并标记 consolidation_pending：{exc}"
            ) from exc
        _ = pending_was  # 保留字段给后续版本使用（暂无分支逻辑）

    bus.emit(
        "memory.profile_updated",
        {"version": new_version, "changed": ["content", "affective_luminance", "source"]},
    )


# --------------------------------------------------------------------------- #
# 巩固调度（云摘要经网关）
# --------------------------------------------------------------------------- #
async def _consolidate(
    session_id: str,
    client: Any,
    data_track: Any,
    store: Any | None,
    cancel_token: Callable[[], bool] | CancellationToken | None,
) -> dict[str, Any]:
    track = data_track if data_track is not None else _default_datatrack()
    store_obj = store if store is not None else _default_store()
    profile = store_obj.load()
    last_ts = float(profile.get("last_consolidated_ts", 0) or 0)
    # 增量：有 ts 的记录只纳入 ts > 上次已巩固位置的新增；无 ts 的记录（历史/测试数据）总是纳入
    all_logs = track.items("session_logs")
    logs: list[dict[str, Any]] = []
    for l in all_logs:
        if not isinstance(l, dict):
            continue
        ts = l.get("ts")
        if ts is None:
            logs.append(l)
        elif float(ts or 0) > last_ts:
            logs.append(l)
    ts_values = [float(l.get("ts", 0) or 0) for l in logs if l.get("ts")]
    max_ts = max(ts_values, default=last_ts)
    raw = _join_session_logs(logs)
    if not raw:
        return {"status": "empty", "session_id": session_id, "reason": "无新增会话原文可巩固"}

    # 出网必过 guard_outbound（gateway 返回 (处置, 处理文本)）。
    verdict, processed = _guard_outbound(raw, session_id)
    if verdict != "cloud_safe":
        return {
            "status": "blocked",
            "session_id": session_id,
            "reason": "出网网关拦截，文本本机留（不调用云 LLM）",
            "guard": verdict,
        }

    messages = [
        ChatMessage(role="system", content=build_consolidation_system_prompt()),
        ChatMessage(role="user", content=processed),
    ]
    completion = await client.complete(messages)
    returned_text = completion.content if completion is not None else ""

    # 回程还原（占位符回填），再解析为候选。
    restored = _guard_inbound(returned_text, session_id)
    candidates = parse_candidates(restored)

    # 优雅退避：用户回来 → cancelled，静默跑完网络 I/O 但**丢弃返回值**，绝不提交/覆盖。
    if cancel_token is not None and (cancel_token() if callable(cancel_token) else bool(cancel_token)):
        return {
            "status": "cancelled",
            "session_id": session_id,
            "candidates": candidates,
            "committed": False,
        }

    apply_profile(candidates, store=store, last_consolidated_ts=max_ts)
    return {
        "status": "ok",
        "session_id": session_id,
        "candidates": candidates,
        "committed": True,
    }


def trigger_consolidation(
    session_id: str,
    *,
    data_track: Any | None = None,
    store: Any | None = None,
    llm_client: Any | None = None,
    cancel_token: Callable[[], bool] | CancellationToken | None = None,
) -> dict[str, Any]:
    """触发一次增量巩固（云摘要经网关出口）。线程安全地跑在后台网络线程。

    返回 dict：``status`` ∈ {ok, blocked, cancelled, empty} 等；成功时附 ``candidates``。
    ``data_track`` / ``store`` / ``llm_client`` 为可注入依赖（测试 mock，不上网）。
    """
    if not session_id:
        raise ValueError("session_id 不能为空")
    client = llm_client if llm_client is not None else _get_llm_client()
    return asyncio.run(_consolidate(session_id, client, data_track, store, cancel_token))


__all__ = [
    "IDLE_CONSOLIDATION_SECONDS",
    "PROFILE_MAX_ENTRIES",
    "CONSOLIDATION_SCHEMA",
    "build_consolidation_system_prompt",
    "CancellationToken",
    "ConsolidationError",
    "ConsolidationCancelled",
    "ConsolidationParseError",
    "ProfileStore",
    "create_client",
    "parse_candidates",
    "apply_profile",
    "trigger_consolidation",
    "_guard_outbound",
    "_guard_inbound",
]
