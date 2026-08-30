# -*- coding: utf-8 -*-
"""出网安全网关 · 占位混淆/还原引擎（M0 / A3）。

纯函数双向替换：出网前把「真名/敏感词」替换成占位符，回程后把占位符还原成真名。

设计要点（第三使用者视角 / AGENTS.md）：
- 纯函数，不读配置不进网络；mapping 由调用方（A5 编排）从 compliance.yaml 的
  obfuscation_mapping 传入，与本模块彻底解耦；
- 替换顺序按 key 长度**降序**（先长后短），避免「我妈妈」里的「我妈」被先替换掉、
  导致长实体漏换（如「我妈」+「我妈妈」并存时，前者先换会把后者吃成一个「妈」剩字）；
- 只替换 mapping 里登记的 key；未知词原样保留；
- 空 mapping 或空文本原样返回；
- 双向 roundtrip：restore(obfuscate(t)) == t（对登记的 key）；
- 混淆/还原均幂等（占位符不在 key 集合时第二次调用不再改动）。

仅标准库（re）；MIT 自写代码，不复制 GPL/AGPL。
"""

from __future__ import annotations

import re


def _desc_keys(mapping: dict[str, str]) -> list[str]:
    """按 key 长度降序返回 mapping 的 key（等长时保持 dict 插入序，不影响正确性）。"""
    return sorted(mapping, key=len, reverse=True)


def obfuscate(text: str, mapping: dict[str, str]) -> str:
    """真名 -> 占位符，出网前调用。

    text:    任意文本。
    mapping: {"真名": "占位符"}。

    返回：替换后的文本。只替换 mapping 里登记的 key；未知词、空 mapping、空文本原样返回。
    实现为单条最长优先正则 + 一次性 sub，左右无重叠、从左到右扫描，天然满足长 key 先替换。
    """
    if not text or not mapping:
        return text
    pattern = re.compile("|".join(re.escape(k) for k in _desc_keys(mapping)))
    return pattern.sub(lambda m: mapping[m.group(0)], text)


def restore(text: str, mapping: dict[str, str]) -> str:
    """占位符 -> 真名，回程后调用。

    text:    云端返回的文本（含占位符）。
    mapping: {"真名": "占位符"}（与 obfuscate 相同的字典，内部反查占位符）。

    返回：还原后的文本。把 mapping 里的占位符替换回对应真名；未知占位符原样保留。
    占位符同样按长度降序替换，避免嵌套占位符（如 User_Kinship 与 User_Kinship_Mother）被短者先吃。
    """
    if not text or not mapping:
        return text
    # 反查：占位符 -> 真名。若两个真名映射到同一占位符（不该出现），后者覆盖前者。
    inverse: dict[str, str] = {}
    for key, value in mapping.items():
        inverse[value] = key
    pattern = re.compile("|".join(re.escape(p) for p in _desc_keys(inverse)))
    return pattern.sub(lambda m: inverse[m.group(0)], text)


def matched_keys(text: str, mapping: dict[str, str]) -> set[str]:
    """返回 obfuscate 实际会替换的 key 集合（最长优先、不重叠、从左到右），供 A4 复用。

    用于在 A4 register 时精确知道「本次出网实际用到了哪些占位符」，
    从而把「我妈」+「我妈妈」并存时只有长 key 被替换的情况正确地只登记长 key 的占位符。

    与 obfuscate 的匹配语义完全一致（单条最长优先正则 = 每个位置先试最长 key，
    命中后前进该 key 长度，否则前进 1 字符）。本函数是纯扫描，不依赖正则引擎。
    """
    if not text or not mapping:
        return set()
    keys = _desc_keys(mapping)
    matched: set[str] = set()
    i, n = 0, len(text)
    while i < n:
        found = None
        for k in keys:
            if text.startswith(k, i):
                found = k
                break
        if found is not None:
            matched.add(found)
            i += len(found)
        else:
            i += 1
    return matched


__all__ = ["obfuscate", "restore", "matched_keys"]
