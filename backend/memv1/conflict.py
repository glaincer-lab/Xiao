"""记忆冲突仲裁决策层（M1-B）。

纯标准库规则引擎，无 LLM、无第三方依赖。

设计边界
--------
本模块**只做规则层的仲裁决策**，不负责「是否发生冲突」的语义抽取（那在候选抽取/
上层）。为了在规则层面可判定地工作，这里对 MemEntry 做轻量归一化：

- 话题槽位：用内置种子话题词典 `_TOPIC_TERMS` 从 ``content`` 里抽取「同一件事」；
  两说共享任一话题词即视为同一个事实槽位。
- 极性：用否定标记 `_NEGATION_MARKERS` 判定「正向 / 负向」声明。

因此，凡是输入须满足 MEMV1_CONTRACT.md 的五要素字段名（dict 或 dataclass 均可），
本模块不绑定 ``backend.memv1.schema`` 的具体类名，避免对并行的 M1-A 产生强依赖。

对外
----
- :func:`classify_conflict`：冲突三协议分级，返回 ``"high"|"low"|"identity"|"none"``。
- :func:`merge_for_retrieval`：检索合并（排序 + 去重），局部>全局、新>旧、行为>声明。
- :class:`WeeklyQuota`：周配额（记忆澄清 + 周期体检合计每周≤3，受全局≤5约束）。
"""

from __future__ import annotations

import datetime as _dt
import re

__all__ = [
    "classify_conflict",
    "merge_for_retrieval",
    "WeeklyQuota",
    "BEHAVIOR_CONFIRM_THRESHOLD",
]

# 行为连续违背同一记忆足以触发「温和确认一次」的次数（规格 §4.2 使用协议写死：3）。
BEHAVIOR_CONFIRM_THRESHOLD = 3

# ---------------------------------------------------------------------------
# 归一化词典（MVP 种子，语义级抽取在上游，这里做规则层槽位/极性判定）
# ---------------------------------------------------------------------------

# 话题种子词典：识别「同一件事」用。命中任一子串即记为该条的话题词。
_TOPIC_TERMS = (
    "咖啡", "茶", "辣", "糖", "酒", "烟", "跑步", "健身", "游泳", "骑行",
    "城市", "上海", "北京", "深圳", "广州", "杭州", "成都", "南京", "武汉",
    "工作", "家", "店", "搬家", "车", "狗", "猫", "游戏", "电影", "音乐",
)

# 身份级变化关键词：搬家 / 换城市 / 换工作 / 家庭 等人生身份属性。
_IDENTITY_KEYWORDS = (
    "搬家", "搬迁", "迁", "移居", "住", "城市", "上海", "北京", "深圳", "广州",
    "杭州", "成都", "工作", "入职", "离职", "换工作", "跳槽", "结婚", "分手",
    "离婚", "出生", "孩子", "小孩", "开店", "关店", "店面", "地址",
)

# 否定 / 拒绝标记：用于判定声明的极性（负向 / 正向）。
_NEGATION_MARKERS = ("不", "没", "别", "无", "忌", "戒", "拒", "禁", "从不", "不喝")

# 作用域 → 局部性权重（局部 > 全局）。
_SCOPE_SPECIFICITY = {"event": 5, "place": 4, "occasion": 3, "period": 2, "global": 1}

# 来源 → 可信权重（行为 > 声明/显式 > 推断）。
_SOURCE_STRENGTH = {"behavior": 3, "explicit": 2, "inferred": 1}


# ---------------------------------------------------------------------------
# 字段读取：兼容 dict 与 dataclass（不依赖具体类名）
# ---------------------------------------------------------------------------

def _get(entry, key, default=None):
    """按契约字段名读取条目字段；dict 用 .get，dataclass 用 getattr。"""
    if isinstance(entry, dict):
        return entry.get(key, default)
    return getattr(entry, key, default)


def _topic_terms(content):
    c = (content or "").lower()
    return tuple(sorted({t for t in _TOPIC_TERMS if t in c}))


def _polarity(content):
    return "negative" if any(m in (content or "") for m in _NEGATION_MARKERS) else "positive"


def _normalize(content):
    return re.sub(r"\s+", "", (content or "").lower())


# ---------------------------------------------------------------------------
# 冲突判定（rule 层）
# ---------------------------------------------------------------------------

def _same_slot(a, b):
    """a、b 是否指涉「同一件事」。命中同一话题词，或都无话题时内容归一化一致。"""
    ta = _topic_terms(_get(a, "content", ""))
    tb = _topic_terms(_get(b, "content", ""))
    if ta and tb:
        return bool(set(ta) & set(tb))
    if not ta and not tb:
        return _normalize(_get(a, "content", "")) == _normalize(_get(b, "content", ""))
    return False


def _contradicts(a, b):
    """同一件事上、极性相反 → 声明与行为（或新旧偏好）相互矛盾。"""
    if not _same_slot(a, b):
        return False
    return _polarity(_get(a, "content", "")) != _polarity(_get(b, "content", ""))


def _is_expired(entry):
    if _get(entry, "status", None) == "expired":
        return True
    detail = _get(entry, "scope_detail", None)
    if isinstance(detail, dict):
        until = detail.get("until") or detail.get("end")
        if until:
            try:
                return _dt.date.fromisoformat(str(until)) < _dt.date.today()
            except (ValueError, TypeError):
                return False
    return False


def _is_identity_change(new, existing):
    """身份级变化：搬迁 / 换城市 / 换工作 / 家庭/常去场所等人生身份属性变更。"""
    content = _get(new, "content", "")
    if any(k in content for k in _IDENTITY_KEYWORDS):
        return True
    scope = _get(new, "scope", "")
    if scope in ("place", "event"):
        return any(_get(e, "scope", "") == "global" for e in existing)
    return False


def _is_high_value(new, existing):
    """沉淀时冲突的高价值判定：高-value 偏好 → 顺口澄清；否则按 scope=period 保守存。"""
    if _get(new, "confirmed", False):
        return True
    try:
        if int(_get(new, "affective_luminance", 0) or 0) >= 3:
            return True
    except (TypeError, ValueError):
        pass
    if _get(new, "source", "") == "explicit":
        return True
    try:
        if float(_get(new, "confidence", 0.0) or 0.0) >= 0.6:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _pick_declaration(entries):
    """从一组条目里挑「声明」：非行为来源，优先显式声明，其次推断。"""
    stmts = [e for e in entries if _get(e, "source", "") != "behavior"]
    if not stmts:
        return None
    return max(stmts, key=lambda e: (_statement_rank(e), _recency_key(e)))


def _statement_rank(entry):
    return {"explicit": 2, "inferred": 1}.get(_get(entry, "source", ""), 0)


# ---------------------------------------------------------------------------
# 检索合并相关的优先级权重
# ---------------------------------------------------------------------------

def _scope_rank(entry):
    return _SCOPE_SPECIFICITY.get(_get(entry, "scope", ""), 0)


def _source_rank(entry):
    return _SOURCE_STRENGTH.get(_get(entry, "source", ""), 0)


def _recency_key(entry):
    s = _get(entry, "effective_at", "") or _get(entry, "effective_at", "")
    if not s:
        return _dt.date.min
    try:
        return _dt.date.fromisoformat(str(s))
    except (ValueError, TypeError):
        return _dt.date.min


def _priority_key(entry):
    """检索优先级：先局部>全局，再新>旧，再行为>声明（与规格列举顺序一致）。"""
    return (_scope_rank(entry), _recency_key(entry), _source_rank(entry))


def _slot_key(entry):
    """合并分组用的槽位键：有话题则用话题集合，否则退化为归一化内容。"""
    terms = _topic_terms(_get(entry, "content", ""))
    if terms:
        return ("topic",) + terms
    return ("content", _normalize(_get(entry, "content", "")))


# ---------------------------------------------------------------------------
# 对外：冲突三协议
# ---------------------------------------------------------------------------

def classify_conflict(new, existing):
    """把新候选记忆与既有记忆的冲突分级。

    返回 ``"high"`` | ``"low"`` | ``"identity"`` | ``"none"``。

    判定顺序（对应 §4.2 三协议）：
    1. 无同类槽位 / 无实质矛盾 -> ``"none"``；
    2. 身份级变化 -> ``"identity"``（记事件 + 一次批量迁移确认）；
    3. 行为类冲突（使用协议）-> 行为连续 ≥3 次违背同一记忆 -> ``"high"``（温和确认
       一次）；否则 ``"low"``（直接服务绝不指责）；
    4. 沉淀协议 -> 高价值偏好 -> ``"high"``（顺口澄清）；否则 ``"low"``
       （按 scope=period 保守存）。
    """
    active = [e for e in existing if not _is_expired(e)]
    same = [e for e in active if _same_slot(new, e)]
    if not same:
        return "none"

    if _is_identity_change(new, same):
        return "identity"

    if _get(new, "source", "") == "behavior":
        # 使用协议
        declared = _pick_declaration(same)
        if declared is None:
            return "low"
        behavior_entries = [new] + [e for e in same if _get(e, "source", "") == "behavior"]
        violations = sum(1 for b in behavior_entries if _contradicts(b, declared))
        return "high" if violations >= BEHAVIOR_CONFIRM_THRESHOLD else "low"

    # 沉淀协议：必须与既有条目存在实际矛盾才算冲突
    contradicted = [e for e in same if _contradicts(new, e)]
    if not contradicted:
        return "none"
    return "high" if _is_high_value(new, contradicted) else "low"


# ---------------------------------------------------------------------------
# 对外：检索合并
# ---------------------------------------------------------------------------

def merge_for_retrieval(candidates):
    """按合并规则对检索候选做排序 + 去重。

    合并规则（§4.2 写死）：局部 > 全局，新 > 旧，行为 > 声明。

    - 同一槽位（话题）内的相互冲突：非待澄清 -> 行为>声明取最强一条；
      待澄清 -> 两说并存于存储，但检索默认返回**声明**（显式/推断），末尾模糊提示
      由上层附加。
    - 跨槽位排序：先按作用域局部性（局部>全局），再按生效时间（新>旧），
      再按来源可信度（行为>声明/推断）。
    """
    groups = {}
    order = []
    for e in candidates:
        slot = _slot_key(e)
        if slot not in groups:
            groups[slot] = []
            order.append(slot)
        groups[slot].append(e)

    reprs = [_reconcile(groups[slot]) for slot in order]
    reprs.sort(key=_priority_key, reverse=True)
    return reprs


def _reconcile(entries):
    """单槽位内的合并：去重 + 冲突裁决，返回该槽位在检索中应呈现的代表条目。"""
    pending = [e for e in entries if _get(e, "status", None) == "pending_clarify"]
    if pending:
        # 待澄清：两说并存（存储层），检索默认返回声明；行为条目不抢占。
        decl = _pick_declaration(entries)
        if decl is not None:
            return decl
        return max(entries, key=_priority_key)
    # 冲突解决：行为 > 声明（同槽内取最强条目）；精确重复亦被去重。
    return max(entries, key=_priority_key)


# ---------------------------------------------------------------------------
# 对外：周配额
# ---------------------------------------------------------------------------

class WeeklyQuota:
    """记忆澄清 + 周期体检 的每周配额（§4.2 配额体系写死）。

    - 分项上限：记忆类澄清 + 周期体检 合计每周 ≤ ``memory_budget``（默认 3）。
    - 全局约束：整体受全局每周询问预算 ≤ ``global_budget``（默认 5）约束。
    - 优先级：冲突澄清 > 周期体检；周期体检在空间紧张时顺延到下一周。

    ``kind`` 固定为 ``"clarify"``（冲突澄清 / 记忆类澄清）与 ``"periodic_check"``
    （周期体检）。``now_fn`` 可注入以便测试与时钟解耦（默认用真实时间）。
    """

    MEMORY_BUDGET = 3
    GLOBAL_BUDGET = 5
    KINDS = ("clarify", "periodic_check")

    def __init__(self, now_fn=None, memory_budget=None, global_budget=None):
        self._now_fn = now_fn or _dt.datetime.now
        self._memory_budget = memory_budget if memory_budget is not None else self.MEMORY_BUDGET
        self._global_budget = global_budget if global_budget is not None else self.GLOBAL_BUDGET
        self._week_key = self._week_key_of(self._now_fn())
        self._used = {"clarify": 0, "periodic_check": 0}
        self._global_used = 0

    @staticmethod
    def _week_key_of(dt):
        """ISO 周键，形如 ``2026-W35``；跨本周即视为新一周。"""
        return dt.strftime("%G-W%V")

    @property
    def memory_used(self):
        return self._used["clarify"] + self._used["periodic_check"]

    def reset_if_new_week(self):
        """若已跨周，清空周内计数并按新一周键重新起算。"""
        wk = self._week_key_of(self._now_fn())
        if wk != self._week_key:
            self._week_key = wk
            self._used = {"clarify": 0, "periodic_check": 0}
            self._global_used = 0

    def remaining(self):
        """本周剩余可消费次数（分项与全局取较小者，不为负）。"""
        self.reset_if_new_week()
        mem_left = max(0, self._memory_budget - self.memory_used)
        global_left = max(0, self._global_budget - self._global_used)
        return min(mem_left, global_left)

    def try_consume(self, kind):
        """尝试消耗一次指定 kind 的配额；超限 / 顺序不对返回 False。"""
        self.reset_if_new_week()
        if kind not in self.KINDS:
            return False
        if self.memory_used >= self._memory_budget:
            return False
        if self._global_used >= self._global_budget:
            return False
        # 优先级：只剩 1 个槽位时留给冲突澄清；周期体检顺延到下一周。
        if kind == "periodic_check" and self.memory_used >= self._memory_budget - 1:
            return False
        self._used[kind] = self._used.get(kind, 0) + 1
        self._global_used += 1
        return True
