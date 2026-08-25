"""Phase 4 端到端验证：路由逻辑 + DSH 桥（命令解析 / 可用性 / 真实调用）。

用法：
  python scripts/verify_phase4.py          # 纯逻辑测试（路由三档 + 桥解析 + 可用性）
  python scripts/verify_phase4.py --live   # 额外跑一次真实 `dsh --profile headless` 调用

退出码：0 = 全部通过；非 0 = 有失败项。
"""
from __future__ import annotations

import asyncio
import os
import sys

# 确保项目根目录在 import 路径中（脚本放在 scripts/ 下）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 强制 UTF-8 输出，避免 Windows GBK 控制台对中文/emoji 报 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from backend.config import config
from backend.router import Router
from backend.bridge.dsh_bridge import DSHBridge


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def check(name: str, got, expected) -> bool:
    ok = got == expected
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got={got!r} expected={expected!r}")
    return ok


def test_router() -> int:
    section("路由分类（三档模式）")
    fails = 0

    r = Router()
    r.set_mode("auto")
    auto_cases = [
        ("帮我写个爬虫抓数据", "dsh"),
        ("帮我实现一个排序函数", "dsh"),
        ("跑一下这个脚本", "dsh"),
        ("帮我查代码里的 bug", "dsh"),
        ("今天天气怎么样", "chat"),
        ("给我讲个笑话", "chat"),
        ("你好", "chat"),
    ]
    for text, exp in auto_cases:
        fails += 0 if check(f"auto·{text[:12]}", r.route(text), exp) else 1

    r.set_mode("chat")
    fails += 0 if check("chat 强制·帮我写代码", r.route("帮我写代码"), "chat") else 1

    r.set_mode("dsh")
    fails += 0 if check("dsh 强制·给我讲个笑话", r.route("给我讲个笑话"), "dsh") else 1

    return fails


def test_bridge() -> int:
    section("DSH 桥：命令解析 + 可用性")
    b = DSHBridge()
    cmd = b._resolve_command(str(config.get("bridge.dsh_command", "dsh")))
    print(f"  解析命令: {cmd}")
    ok = b.is_available()
    print(f"  is_available: {ok}")
    if not ok:
        print("  ⚠ DSH 命令未找到，真实调用会失败（请确认 dsh 在 PATH 中）")
    # 解析成功且可用，才算这一项通过
    return 0 if ok else 1


async def test_live() -> int:
    section("真实 DSH headless 调用（端到端）")
    b = DSHBridge()
    if not b.is_available():
        print("  SKIP：DSH 命令不可用")
        return 0
    task = "在 03_Workspace 里写一个 phase4_check.txt，内容只有一行：小二在线"
    print(f"  任务: {task}")
    print("  调用 DSH 中（可能耗时数十秒）…")
    try:
        out = await b.run(task)
        print(f"  DSH 返回:\n{out}")
        ok = bool(out.strip())
        print(f"  [{'PASS' if ok else 'FAIL'}] 返回非空")
        return 0 if ok else 1
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] 异常: {e}")
        return 1


def main() -> int:
    fails = test_router() + test_bridge()
    if "--live" in sys.argv:
        fails += asyncio.run(test_live())
    section("汇总")
    if fails == 0:
        print("  全部通过 ✅")
        return 0
    print(f"  {fails} 项失败 ❌")
    return 1


if __name__ == "__main__":
    sys.exit(main())
