#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模块边界纪律断言脚本（T0 · P0 结构锁定，对应结构规划 S6 / M0-core §5·§7）。

目的：静态断言「无跨模块直调对方核心函数的 import」。
依据：
    - M0-core.md §5  事件总线使用规范：跨模块一律走 backend/event_bus.py 的 bus.on/emit，
                      禁止跨模块直调核心函数。
    - M0-core.md §7  验收断言：grep 断言无「跨模块直调对方模块核心函数」的 import。
    - _audit/结构规划-2026-08-30.md S6：模块边界纪律（禁止跨模块直调），落地为断言脚本。

模块单位：backend 下的顶层模块（backend.event_bus / backend.core / backend.memv4）
与子包（backend.asr / backend.llm / backend.gateway / backend.session ...）。

白名单（跨模块 import 视为合法，不告警）：
    1) 同包内部引用：源与目标同属一个子包 / 顶层模块族；
    2) 记忆域内部互通：memv1/memv2/memv4/memory 之间（同属 M1 记忆数据模型）；
    3) 目标为基础设施/白名单模块：event_bus、session 状态流、config、errors、
       settings_schema、config_guard、perms、rules、memory、memv4、offline、provider_test；
    4) 源为装配层/入口模块：main、core、agent、router、tasks、launcher、
       provider_test、offline、bridge 及其入口；
    5) 源为包级 __init__.py 合法装配；
    6) 目标为某子包的「接口/装配层文件」：base.py、factory.py、chain.py、constants.py。

不在上述白名单内、且目标确为对方「业务实现文件」的跨模块 import，输出为「待人工复核」
（含倾向判断），不直接判 FAIL——因为可能在架构上属合理装配或必经层，需人工定夺。
可用 --strict 把「待人工复核」也视为违规并返回 FAIL，供后续 AI 协作/CI 强制使用。

本脚本只读、不修改任何代码。

运行：  .venv/Scripts/python.exe scripts/audit_module_boundaries.py [--strict]
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# Windows 控制台默认 GBK，重配为 utf-8 保证中文输出可读（人话报错，见 M0-core §5）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND = PROJECT_ROOT / "backend"
SKIP_DIRS = {"__pycache__", ".venv", ".git", "node_modules"}


# ---------------------------------------------------------------------------
# 白名单定义（均为 backend 内部的模块/源文件）
# ---------------------------------------------------------------------------

# 1) 目标为「基础设施/白名单模块」：被任意模块引用均合理。
INFRA_MODULES_PREFIX = (
    "backend.event_bus",       # 跨模块唯一通信信道（总线）
    "backend.session",         # 前端会话状态事件流（session/state.py）
    "backend.config",
    "backend.errors",
    "backend.settings_schema",
    "backend.config_guard",
    "backend.perms",
    "backend.rules",
)

# 2) 目标为「backend 下被广泛当作基础设施复用的单文件模块」（非业务核心）。
INFRA_SINGLE_MODULES = {
    "backend.config",
    "backend.errors",
    "backend.memory",        # v3 显式记忆存储（工具 remember 直调，见 EVENT_REGISTRY 命令通道）
    "backend.memv4",         # 记忆数据层（被 M1 各层/agent 复用为存储基础设施）
    "backend.offline",
    "backend.provider_test",
    "backend.rules",
    "backend.perms",
}

# 3) 源为「装配层/入口」模块：承担装配职责，允许引用其它模块。
ASSEMBLY_SOURCES = {
    "backend.main",
    "backend.core",
    "backend.agent",
    "backend.router",
    "backend.tasks",
    "backend.launcher",
    "backend.provider_test",
    "backend.offline",
    "backend.bridge",                 # 包级装配
    "backend.bridge.dsh_bridge",
    "backend.bridge.dsh_web_bridge",
}

# 4) 目标为某子包的「接口层/装配层文件」（协议或装配，非具体业务实现）：
INTERFACE_FILE_BASENAMES = {"base", "factory", "chain", "constants"}

# 5) 记忆域：同属 M1 记忆数据模型，内部互通允许。
MEMORY_DOMAIN = ("backend.memv1", "backend.memv2", "backend.memv4", "backend.memory")


# ---------------------------------------------------------------------------
# 模块 / 文件解析
# ---------------------------------------------------------------------------

def module_of(rel: str) -> str:
    """backend/llm/base.py -> backend.llm.base"""
    return rel[:-3].replace("/", ".")


def top_pkg(mod: str) -> str:
    """取「顶层分组」：backend.llm.base -> backend.llm ；backend.memv4 -> backend.memv4。
    backend 下所有模块形如 backend.<第一层>[.<更细>]，第一层即顶层分组。"""
    parts = mod.split(".")
    return ".".join(parts[:2])


def basename(mod: str) -> str:
    """模块名最后一段：backend.llm.base -> base"""
    return mod.rsplit(".", 1)[-1]


def is_same_package(src_mod: str, target_mod: str) -> bool:
    return top_pkg(src_mod) == top_pkg(target_mod)


# 已人工核定为「合理装配 / 安全必经层」的精准豁免（键 = 源模块 -> 目标模块，非通配）。
# 命中即算允许，不进入「待人工复核」，也不会掩盖其它新的跨模块引用（新引用仍会被检出）。
RATIONALIZED_ALLOW = {
    "backend.audio.wake_omni -> backend.asr.omni": "音频唤醒扩展复用 ASR Omni 引擎类，属感知层内部协作（非业务模块间通信）。[人工核定：合理装配]",
    "backend.memv1.consolidate -> backend.gateway": "记忆巩固出网必须经过网关 guard_outbound/guard_inbound（S4 安全必经层，所有云调用前强制层）。[人工核定：合理装配]",
    "backend.audit.xiao_audit -> backend.gateway.gateway": "审计落盘前复用网关 guard_outbound 做本地脱敏（R2 防隐私裸奔；网关属全局安全基础设施，非业务模块间直调）。[人工核定：合理装配]",
    "backend.audit.xiao_audit -> backend.gateway.load_config": "审计脱敏读取 compliance 的 local_only_keywords（R2；复用网关安全配置，非业务模块直调）。[人工核定：合理装配]",
}

# 若出现 RATIONALIZED_ALLOW 未覆盖的新待复核项，用此注释展示倾向（保留通用说明）。
REVIEW_FALLBACK_NOTE = "该处需人工判断：是否属合理装配/必经层，还是真违规（应走总线）。"


def is_allowed(src_mod: str, target_mod: str) -> tuple[bool, str]:
    """返回 (是否允许, 说明)。"""
    target_file = basename(target_mod)
    src_top = top_pkg(src_mod)
    target_top = top_pkg(target_mod)

    # (0) 已人工核定为「合理装配 / 安全必经层」的精准豁免（仅此处，见 RATIONALIZED_ALLOW）
    key = src_mod + " -> " + target_mod
    if key in RATIONALIZED_ALLOW:
        return True, RATIONALIZED_ALLOW[key]

    # (1) 同包内部引用
    if is_same_package(src_mod, target_mod):
        return True, "同包内部引用"

    # (2) 记忆域内部互通
    if src_top in MEMORY_DOMAIN and target_top in MEMORY_DOMAIN:
        return True, "记忆域内部互通（数据模型/存储）"

    # (3) 目标为基础设施/白名单模块
    for p in INFRA_MODULES_PREFIX:
        if target_mod == p or target_mod.startswith(p + "."):
            return True, "目标为基础设施/白名单模块（event_bus/session/config 等）"
    if target_mod in INFRA_SINGLE_MODULES:
        return True, "目标为可复用单文件模块（config/memory/memv4 等）"

    # (4) 源为装配层/入口模块
    if src_mod in ASSEMBLY_SOURCES:
        return True, "源为装配层/入口模块"

    # (5) 目标为某子包的「接口层文件」（base/factory/chain/constants）
    if target_file in INTERFACE_FILE_BASENAMES:
        return True, "目标为子包接口/装配层文件（base/factory/chain/constants）"

    return False, "跨模块引用对方业务实现文件（非接口/装配层）"


def find_python_files() -> list[Path]:
    return [p for p in BACKEND.rglob("*.py") if p.is_file() and not any(part in SKIP_DIRS for part in p.parts)]


def collect_imports(tree: ast.AST) -> list[tuple[str, str, int]]:
    """收集所有目标为 backend.* 的 import：返回 (module, names, lineno)"""
    out: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if not mod.startswith("backend"):
                continue
            names = ", ".join(a.name for a in node.names)
            out.append((mod, names, node.lineno))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("backend"):
                    out.append((a.name, "", node.lineno))
    return out


def main() -> int:
    strict = "--strict" in sys.argv
    argv_clean = [a for a in sys.argv if a != "--strict"]

    py_files = sorted(find_python_files())
    scanned_files = [p.relative_to(PROJECT_ROOT).as_posix() for p in py_files]

    whitelist_hits: list[str] = []
    pending_review: list[str] = []

    for path in py_files:
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        src_mod = module_of(rel)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            pending_review.append("[语法错误] " + rel + ": " + str(exc) + " （无法解析，请人工复核）")
            continue

        for mod, names, lineno in collect_imports(tree):
            allowed, reason = is_allowed(src_mod, mod)
            line_text = "{0}:{1}: from {2} import {3}".format(
                rel, lineno, mod, names if names else "(module)")
            if allowed:
                whitelist_hits.append(line_text + "  ->  允许：" + reason)
            else:
                key = src_mod + " -> " + mod
                note = REVIEW_FALLBACK_NOTE
                pending_review.append(line_text + "  ->  待人工复核：" + reason + "。" + note)

    # ------------------------------------------------------------------
    # 报告
    # ------------------------------------------------------------------
    print("=" * 76)
    print("模块边界纪律断言（T0 · P0 结构锁定 / 结构规划 S6 · M0 §5/§7）")
    print("=" * 76)

    print()
    print("【一】扫描文件清单（共 %d 个）" % len(scanned_files))
    for s in scanned_files:
        print("  -", s)

    print()
    print("【二】白名单命中（符合允许规则的跨模块 import，共 %d 条）" % len(whitelist_hits))
    for h in whitelist_hits:
        print("  ", h)

    print()
    print("【三】潜在越界 / 待人工复核 import（共 %d 条）" % len(pending_review))
    if pending_review:
        for v in pending_review:
            print("  ", v)
    else:
        print("  （无）")

    print()
    print("【四】白名单说明")
    print("  - 允许目标：event_bus、session 状态流、config/errors/settings_schema/"
          "config_guard/perms/rules、memory/memv4/offline/provider_test（全局基础设施）。")
    print("  - 允许源：main/core/agent/router/tasks/launcher/bridge（装配层/入口）。")
    print("  - 允许目标文件：各子包 base/factory/chain/constants（接口/装配层）。")
    print("  - 允许同包内部引用与记忆域（memv1/memv2/memv4/memory）内部互通。")
    print("  - Python 标准库 / 第三方库 / backend 之外的引用不在断言范围。")

    print()
    print("【五】结论")
    if pending_review:
        if strict:
            print("  FAIL（--strict）：存在 %d 处跨模块引用对方业务实现文件，需人工复核。" % len(pending_review))
            return 1
        print("  PASS（含 %d 项待人工复核）：未发现【确证】跨模块直调对方核心函数；" % len(pending_review))
        print("        以下 %d 处跨模块 import 不在白名单，需老板定夺其属合理装配还是真违规：" % len(pending_review))
        for v in pending_review:
            print("         -", v.split("  ->  ")[0])
        return 0
    print("  PASS：未发现跨模块直调对方核心函数的 import（全部为白名单/同包/装配层/接口层引用）。")
    return 0


def src_top(mod: str) -> str:
    return top_pkg(mod)


if __name__ == "__main__":
    sys.exit(main())
