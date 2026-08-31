"""M1-E 记忆读出侧：检索注入层 + 呈现轨 180 天滤镜 + 任务态例外。

决定「小二把什么记忆喂给 LLM」。与 `backend/memory.py`（v3 简化版）并存且独立，
该新层本意替换/增强 `MemoryStore.context_text` 的注入方式（但不删后者）。

双轨读出：
- **数据轨（信息检索 / 任务）**：直接读原始细节，供干活 —— 任务态直通。
- **呈现轨（日常情感 / 健忘）**：>180 天记忆在 Context 注入层**硬截断**原始细节，
  只注入巩固摘要 + 情感高光标签 —— **架构级保证**：LLM 看不到原始层。

任务态例外：
- 动词双信号判定（`ACTION_VERBS_TASK` / `ACTION_VERBS_RECALL`，首版词表，可配置）；
- **安全默认「不确定 → 呈现轨（recall）」**（结构规划 D1/决策 4.1 收窄：仅明确信息检索动词走数据轨直通，模糊请求不穿透 180 天情感滤镜）。

升轨只读面板（隔离，v4.1.1 关键）：捞出的原始明文经**显式沙盒隔离面板**渲染（只读，
MVP 降级为 Markdown 代码块），该文本**不对 LLM 开放检索接口（Context Retrieval
Window）**——降级的是渲染形式，不是隔离原则。`render_sandbox_panel` 产出的文本带
`SANDBOX_MARKER`，而 `build_injection` 从结构上跳过已隔离记录，两者永不串轨。

仅标准库；消费的条目（Entry）为契约五要素字段（`id/content/effective_at/...`），
兼容 dict 或 dataclass（attribute）访问，故不硬 import `memv4.MemEntry`（由调用方
注入来源 `set_entry_provider`）。`summary` / `summary_text` / `consolidated` 为
呈现轨「巩固摘要」的读取约定（由 M1-C 巩固层填充）；缺失时用中性占位，绝不落回原始
`content`，从而保证 180 天滤镜下原始细节不泄露。
"""
from __future__ import annotations

import datetime as _dt
from typing import Callable, Iterable, Optional

# 180 天滤镜阈值（天）——呈现轨硬截断
DAYS_180 = 180

# 隔离标记：升轨只读沙盒面板渲染文本必带此前缀；注入层从结构上排除含此标记的文本
SANDBOX_MARKER = "[SANDBOX_READONLY]"

# 安全默认态：不确定 → 呈现轨（recall），不直通数据轨（结构规划 D1/决策 4.1 收窄）。
# DEFAULT_TASK_STATE 保留为历史别名（值已改为 recall），供旧代码/测试向后兼容。
DEFAULT_STATE = "recall"
DEFAULT_TASK_STATE = DEFAULT_STATE

# 首版动词清单（可配置，进 config；灰度期调优，见 M1-memory.md §8 开放问题 1）
ACTION_VERBS_TASK = (
    "帮我", "翻出", "调出", "查找", "搜索", "搜", "查一下", "查", "打开",
    "导出", "生成", "整理", "排查", "修复", "检查", "列出", "提取", "分析",
    "对比", "检测", "设置", "计算", "写出", "找", "报错", "日志",
)
ACTION_VERBS_RECALL = (
    "还记得", "记不记得", "记得吗", "想想", "想起", "回忆",
    "叫什么名字", "叫什么", "记不记得起", "还记得吗", "还能记住",
)

_INJECTION_HEADER = "以下是「小二」跨会话记忆里的相关线索，相关时自然参考，不要逐条复述："
_DEFAULT_FUZZY_SUMMARY = "一条超过 180 天的记忆（细节已随情感淡忘）"


# ---------------------------------------------------------------------------
# 请求分类：动词双信号 + 安全默认
# ---------------------------------------------------------------------------

def _normalize(text: object) -> str:
    return str(text or "").strip().lower()


def _match_any(text: str, verbs: Iterable[str]) -> bool:
    return any(v in text for v in verbs)


def is_recall_or_task(text: str) -> str:
    """判定请求属于 `"task"` / `"recall"` / `"unknown"`。

    动词 + 上下文双信号；**安全默认由 `resolve_state` 将 `"unknown"` 归一为 `"task"`**。
    - 命中任务动词（`翻出/查/找/搜/报错`...）→ `"task"`（动作指令优先于仅追忆）。
    - 否则命中追忆短语（`还记得/记不记得/叫什么名字`...）→ `"recall"`。
    - 两者皆无 → `"unknown"`（按决策 4.1 收窄：默认不直通数据轨）。
    """
    text = _normalize(text)
    if _match_any(text, ACTION_VERBS_TASK):
        return "task"
    if _match_any(text, ACTION_VERBS_RECALL):
        return "recall"
    return "unknown"


def resolve_state(state: str) -> str:
    """将 `is_recall_or_task` 的三态归一到消费态（"task" | "recall"）。

    安全默认（决策 4.1 收窄）：`"unknown"` → `"recall"`（呈现轨，180 天滤镜保护），
    不直通数据轨；仅明确信息检索动词命中才走 `"task"`。`build_injection` 内部即用本函数。
    """
    return state if state in ("task", "recall") else DEFAULT_STATE


# ---------------------------------------------------------------------------
# 条目来源注入（生产接线用；缺省无来源则不注入任何记忆）
# ---------------------------------------------------------------------------

_entry_provider: Optional[Callable[[], Iterable]] = None


def set_entry_provider(provider: Callable[[], Iterable]) -> None:
    """设置记忆条目来源：无参回调，返回一个可迭代的 Entry（dict 或 dataclass）。"""
    global _entry_provider
    _entry_provider = provider


def reset_entry_provider() -> None:
    """清空条目来源（回到「无记忆可读」状态），供测试或停用时复位。"""
    global _entry_provider
    _entry_provider = None


def _collect_entries() -> list:
    if _entry_provider is None:
        return []
    return list(_entry_provider())


# ---------------------------------------------------------------------------
# 升轨只读沙盒面板（隔离通道：只读明文，不接 LLM）
# ---------------------------------------------------------------------------

_sandboxed_ids: set[str] = set()


def mark_sandboxed(record_id: str) -> None:
    """将某条原始记录标记为「已进入升轨只读面板」——该记录从此对 LLM 检索隔离。

    调用时机：升轨流程把原始明文交给 `render_sandbox_panel` 渲染前的瞬间。
    """
    _sandboxed_ids.add(str(record_id))


def reset_sandboxed() -> None:
    """清空隔离记录集（测试 / 复位用）。"""
    _sandboxed_ids.clear()


def is_sandboxed(entry: object) -> bool:
    """该条目是否已被隔离（不进 LLM 注入）。支持 id 注册 + 条目自带 `sandbox` 标记。"""
    eid = _get(entry, "id", None)
    if eid is not None and str(eid) in _sandboxed_ids:
        return True
    for k in ("sandbox", "sandboxed", "isolated"):
        if _get(entry, k, False):
            return True
    return False


def render_sandbox_panel(raw_text: str) -> str:
    """升轨只读沙盒面板（MVP 降级：Markdown 代码块）渲染。

    带 `SANDBOX_MARKER` 前缀，语义上只作屏幕只读展示；**严禁**将该返回值
    送入 `build_injection` / `render_injection` 的 LLM 通道。
    """
    raw_text = str(raw_text or "")
    return f"{SANDBOX_MARKER}\n```\n{raw_text}\n```"


# ---------------------------------------------------------------------------
# 注入渲染（纯函数：给定请求 + 条目 + 参考日期 → LLM 注入文本）
# ---------------------------------------------------------------------------

def build_injection(user_request: str) -> str:
    """契约：返回拼进系统提示词的记忆文本；无可用记忆时返回空串。

    读取 `set_entry_provider` 注入的来源；默认安全策略 `resolve_state` 归一请求态。
    """
    return render_injection(user_request, _collect_entries())


def render_injection(user_request: str, entries: Iterable, today: _dt.date | None = None) -> str:
    """给定用户请求与条目集合，渲染注入文本（纯函数，`today` 可注入便于测试）。

    - 任务态（仅明确信息检索动词命中）：数据轨直通，注入原始细节，不受 180 天滤镜拦截
      （B 型任务检索不走情感滤镜，见 EVAL.md 场景三断言 3）。
    - 非任务态（recall）：>180 天条目只注入巩固摘要 + 情感高光，原始细节硬截断。
    - 已隔离（sandboxed）条目一律跳过：原始明文只进沙盒面板，不进 LLM。
    """
    today = today or _dt.date.today()
    state = resolve_state(is_recall_or_task(user_request))
    task_mode = state != "recall"
    lines: list[str] = []
    for entry in entries or []:
        if is_sandboxed(entry):
            continue
        if _is_expired(entry, today):
            continue
        raw = _get(entry, "content", "")
        if not isinstance(raw, str) or not raw.strip():
            continue
        if task_mode:
            lines.append(_format_raw(raw, entry))
        else:
            age = _age_days(entry, today)
            if age is not None and age > DAYS_180:
                lines.append(_format_fuzzy(entry, age))
            else:
                lines.append(_format_raw(raw, entry))
    if not lines:
        return ""
    return _INJECTION_HEADER + "\n" + "\n".join(lines)


# ---- 格式化 ----

def _format_raw(content: str, entry: object) -> str:
    return f"- {content.strip()}"


def _format_fuzzy(entry: object, age: int) -> str:
    """呈现轨 180 天滤镜：只给巩固摘要 + 情感高光标签，绝不包含原始 `content`。"""
    summary = _summary_of(entry)
    lum = _get(entry, "affective_luminance", 0)
    try:
        lum = int(lum)
    except (TypeError, ValueError):
        lum = 0
    if lum > 0:
        tag = f"（情感高光 {lum}/5，Affective_Tags: HIGH_LIGHT_{lum}）"
    else:
        tag = "（细节已随 180 天情感淡忘，仅保留印记）"
    return f"- {summary}{tag}"


def _summary_of(entry: object) -> str:
    """读取呈现轨「巩固摘要」；缺失时给中性占位，绝不落回原始 `content`。"""
    for k in ("summary", "summary_text", "consolidated", "consolidated_summary"):
        v = _get(entry, k, "")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return _DEFAULT_FUZZY_SUMMARY


# ---- 通用字段读取（dict 或 dataclass 均可）----

def _get(entry: object, key: str, default: object = "") -> object:
    if isinstance(entry, dict):
        return entry.get(key, default)
    return getattr(entry, key, default)


def _age_days(entry: object, today: _dt.date) -> Optional[int]:
    d = _parse_date(_get(entry, "effective_at", ""))
    if d is None:
        return None
    return max(0, (today - d).days)


def _parse_date(value: object) -> Optional[_dt.date]:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    try:
        return _dt.date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _is_expired(entry: object, today: _dt.date | None = None) -> bool:
    """条目已过期则不注入（status=expired，或 scope_detail.until 已过）。"""
    today = today or _dt.date.today()
    if _get(entry, "status", "") == "expired":
        return True
    detail = _get(entry, "scope_detail", {})
    if isinstance(detail, dict):
        until = detail.get("until")
        if until:
            d = _parse_date(until)
            if d is not None and d < today:
                return True
    return False


__all__ = [
    "DAYS_180",
    "SANDBOX_MARKER",
    "DEFAULT_STATE",
    "DEFAULT_TASK_STATE",
    "ACTION_VERBS_TASK",
    "ACTION_VERBS_RECALL",
    "is_recall_or_task",
    "resolve_state",
    "build_injection",
    "render_injection",
    "set_entry_provider",
    "reset_entry_provider",
    "mark_sandboxed",
    "reset_sandboxed",
    "is_sandboxed",
    "render_sandbox_panel",
]
