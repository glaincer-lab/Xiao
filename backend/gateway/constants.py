# -*- coding: utf-8 -*-
"""出网安全网关 · 共享常量（M0）。

存放跨模块引用的**不可配置硬不变式**，做单一来源，避免多处硬编码漂移。
目前含自伤红线关键词——任何语境都不得出网（见 MEMORY/AGENTS 红线，不因本阶段改变）。
仅标准库；MIT。
"""

from __future__ import annotations

# 自伤红线（不可配置、不可放行）。semantic_filter 与 gateway 共同引用这一份，杜绝双源漂移。
SELF_HARM_KEYWORDS: tuple[str, ...] = ("自杀", "不想活了")


__all__ = ["SELF_HARM_KEYWORDS"]
