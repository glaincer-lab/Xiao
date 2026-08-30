"""出网安全网关 · 黑词检测（M0 / A2）。

纯函数 detect_blocked：给定一段文本与黑词表，判断是否命中任一黑词。
命中且未被豁免时返回该黑词（调用方据此把整条文本留本机，零云调用）；
未命中或已豁免时返回 None。

设计要点（第三使用者视角 / AGENTS.md）：
- 纯函数，不读取任何配置文件；黑词由调用方（A5 编排）从 compliance.yaml 传入，
  与本模块彻底解耦（keywords / allow_words 均由调用方传入）；
- 大小写不敏感（针对英文词，如 bank / BANK / Bank 都命中）；
- 中文按子串匹配，能力覆盖「我的密码是123」这类把黑词嵌在句中的情况；
- 预编译正则缓存：同一批黑词只编译一次，长文本反复调用不重复编译。

消歧设计（解决「密码学 / 密钥管理」误拦，业界 Allow & Deny Lists 思路）：
- 不传 allow_words 时，行为与朴素子串匹配一致（黑词命中即拦）；
- 一旦传入 allow_words（误拦豁免词），黑词命中位置若**完整落在**某个豁免词的
  区间内，则视为常规词放行（例如豁免词含「密码学」「密钥管理」时）：
      「学习密码学」     -> 「密码」被「密码学」覆盖 -> None（不误拦）
      「密钥管理是常识」 -> 「密钥」被「密钥管理」覆盖 -> None
      「我的密码是123」 -> 「密码」无覆盖          -> 「密码」（必拦）
  这样在「敏感信息必拦」与「正常复合词不误拦」之间取得平衡。豁免词同样不做
  大小写敏感判断（英文豁免词也可生效）。

仅标准库（re / functools）；MIT。
"""

from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=None)
def _bound_keyword(kw: str) -> str:
    """为纯 ASCII 字母词加词边界，避免英文子串误伤（bank 不命中 bankruptcy/banker）。

    中文没有词边界概念，保持子串匹配（如「密码」命中「密码学」由 allow_words 消歧）。
    """
    if kw and kw.isascii() and kw[0].isalpha():
        return r"\b" + re.escape(kw) + r"\b"
    return re.escape(kw)


@lru_cache(maxsize=None)
def _compile_keywords(keywords: tuple[str, ...]) -> re.Pattern[str] | None:
    """把黑词表编译成一条组合正则（忽略大小写，中文字串 / 英文按词边界）。

    keywords 为空时返回 None（调用方据此直接判不命中）。
    lru_cache 以 tuple 为键，同一批黑词只编译一次。
    每个词都用 re.escape 包裹，避免把黑词里的正则元字符当作语法。
    """
    if not keywords:
        return None
    return re.compile("|".join(_bound_keyword(kw) for kw in keywords), re.IGNORECASE)


def _word_spans(text: str, word: str) -> list[tuple[int, int]]:
    """找 word 在 text 中的全部出现区间（起始, 结束）。

    英文单词用词边界（大小写不敏感），避免豁免/命中在 bankruptcy/banker 内误判；
    中文与含空格短语按子串（保持与词边界命中判定一致，避免覆盖判定漂移）。
    """
    if word and word.isascii() and word[0].isalpha() and not re.search(r"\s", word):
        pattern = re.compile(r"\b" + re.escape(word) + r"\b", re.IGNORECASE)
        return [(m.start(), m.end()) for m in pattern.finditer(text)]
    low_text = text.lower()
    needle = word.lower()
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        idx = low_text.find(needle, start)
        if idx < 0:
            break
        spans.append((idx, idx + len(word)))
        start = idx + 1
    return spans


def _find_spans(text: str, words: list[str]) -> list[tuple[int, int]]:
    """在 text 中找到 words 中每个词的全部出现区间（起始, 结束）。"""
    spans: list[tuple[int, int]] = []
    for word in words:
        if word:
            spans.extend(_word_spans(text, word))
    return spans


def _covered_by_allowed(start: int, end: int, allowed: list[tuple[int, int]]) -> bool:
    """命中区间 [start, end) 是否完整落在某个豁免词区间内。"""
    for a, b in allowed:
        if a <= start and end <= b:
            return True
    return False


def detect_blocked(text: str, keywords: list[str], allow_words: list[str] | None = None) -> str | None:
    """检测 text 是否命中任一黑词。

    text:        待检测文本（任意字符串）。
    keywords:    黑词表（来自 compliance.yaml 的 local_only_keywords）。
    allow_words: 误拦豁免词（可选）。命中黑词的位置若被某个豁免词完整覆盖，
                 则该命中放行；不传则等价于朴素子串匹配。

    返回：
        命中的关键词原文（str，取自 keywords 表）；未命中、已豁免或 keywords 为空时返回 None。
    """
    if not keywords or not text:
        return None

    allowed = _find_spans(text, allow_words) if allow_words else None

    pattern = _compile_keywords(tuple(keywords))
    if pattern is None:
        return None

    search_from = 0
    while True:
        match = pattern.search(text, search_from)
        if match is None:
            return None
        start, end = match.start(), match.end()
        if not (allowed and _covered_by_allowed(start, end, allowed)):
            # 命中的可能是表内词的大小写变体（如 BANK -> bank），反查表返回原文。
            hit = match.group(0)
            for kw in keywords:
                if kw.lower() == hit.lower():
                    return kw
            return hit
        search_from = end


__all__ = ["detect_blocked"]
