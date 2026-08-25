"""Phase 6 后端验证：AWAIT_APPROVAL 状态 + 审批词表判定（拒绝优先）。

用法：python scripts/verify_phase6.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from backend.config import config
from backend.session.state import State
from backend.core import Pipeline


def check(name: str, got, expected) -> int:
    ok = got == expected
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got={got!r} expected={expected!r}")
    return 0 if ok else 1


def main() -> int:
    fails = 0
    print("=== 状态与配置 ===")
    fails += check("State.AWAIT_APPROVAL", State.AWAIT_APPROVAL.value, "await_approval")
    fails += check("approval.enabled", config.get("approval.enabled", True), True)

    allow = [p for p in (config.get("approval.allow_phrases", []) or []) if p]
    deny = [p for p in (config.get("approval.deny_phrases", []) or []) if p]
    print(f"  允许词: {allow}")
    print(f"  拒绝词: {deny}")

    print("=== 审批判定（拒绝优先） ===")
    cases = [
        ("允许", "allowed-once"),
        ("可以", "allowed-once"),
        ("好，允许你干", "allowed-once"),
        ("拒绝", "rejected"),
        ("不行", "rejected"),
        ("不可以", "rejected"),   # 关键：不能被「可以」误判为允许
        ("我不允许", "rejected"),  # 关键：不能被「允许」误判为允许
        ("算了别做", "rejected"),
        ("今天天气", None),        # 未识别 -> 重新询问
        ("", None),
    ]
    for text, exp in cases:
        got = Pipeline._approval_decision(text, allow, deny)
        fails += check(f"判定·{text or '(空)'}", got, exp)

    print("=== 汇总 ===")
    if fails == 0:
        print("  全部通过 ✅")
    else:
        print(f"  {fails} 项失败 ❌")
    return fails


if __name__ == "__main__":
    sys.exit(main())
