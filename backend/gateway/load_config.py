"""出网安全网关 · 合规配置加载（M0 / A1）。

从 compliance.yaml 读取合规配置并做完整性/类型校验，
供 A2（黑词引擎）/ A3（混淆引擎）/ A4 / A5（网关编排）读取同一份契约字段。

设计要点（第三使用者视角，见 AGENTS.md）：
- 默认路径基于本模块文件位置计算（而不是当前工作目录），在任何机器、任何启动
  目录下都能找到自带配置，做到「装完就能跑」；
- 字段名是跨任务契约（见 _M0-tasks/A1-config-schema.md），本模块只校验、不改名、不擅自增删；
- 报错全部为人话：先说「哪里出了问题」，再说「下一步怎么办」，不抛裸异常堆栈。

仅依赖标准库 + PyYAML；本模块 MIT。
"""

from __future__ import annotations

from pathlib import Path

import yaml

# 自带默认配置文件：相对本模块文件位置计算（非 CWD），任何机器可直接用。
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "compliance.yaml"

# 契约字段 → 期望类型。字段名与 A2-A5 的读取方绑定，不可改，只可增。
_REQUIRED_FIELDS: dict[str, type] = {
    "enabled": bool,
    "local_only_keywords": list,
    "obfuscation_mapping": dict,
    "suggested_entities_max": int,
    "debug_log": bool,
}

# 用于报错提示的默认示例，帮使用者快速补齐缺失字段。
_DEFAULT_SNIPPET = """compliance_gateway:
  enabled: true
  local_only_keywords:
    - "身份证"
    - "密码"
    - "密钥"
    - "bank"
    - "自杀"
    - "不想活了"
  obfuscation_mapping: {}
  suggested_entities_max: 5
  debug_log: false"""


class ComplianceConfigError(Exception):
    """合规配置缺失或类型错误的统一异常；message 为可直接展示的中文人话。"""


def _type_ok(value: object, expected: type) -> bool:
    """按契约校验字段类型；其中 int 字段刻意排除 bool（bool 是 int 的子类）。"""
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, expected)


def _validate(cfg: object, source: str) -> dict:
    """校验：顶层段存在、契约字段齐全且类型正确。返回原 dict（保留用户额外字段）。"""
    if cfg is None:
        raise ComplianceConfigError(
            f"""配置文件 {source} 是空的（没有任何内容）。
请把下面的默认配置写入该文件后重试：
{_DEFAULT_SNIPPET}"""
        )
    if not isinstance(cfg, dict):
        raise ComplianceConfigError(
            f"""配置文件 {source} 的顶层不是键值结构（实际是 {type(cfg).__name__}）。
出网网关配置要求顶层是一组「字段：值」，请改回下面的格式：
{_DEFAULT_SNIPPET}"""
        )

    gw = cfg.get("compliance_gateway")
    if gw is None:
        raise ComplianceConfigError(
            f"""配置文件 {source} 缺少顶层字段 'compliance_gateway'（出网网关配置段）。
请在文件里补上这一段：
{_DEFAULT_SNIPPET}"""
        )
    if not isinstance(gw, dict):
        raise ComplianceConfigError(
            f"""配置文件 {source} 的 'compliance_gateway' 应当是键值分组，
实际却是 {type(gw).__name__}。请把它改成一组字段（参考下面示例）：
{_DEFAULT_SNIPPET}"""
        )

    missing = [key for key in _REQUIRED_FIELDS if key not in gw]
    if missing:
        raise ComplianceConfigError(
            f"""配置文件 {source} 缺少字段：{', '.join(missing)}。
这些字段是出网网关的契约字段，不能删。请参考下面的默认示例补齐：
{_DEFAULT_SNIPPET}"""
        )

    for key, expected in _REQUIRED_FIELDS.items():
        value = gw[key]
        if not _type_ok(value, expected):
            hint = "（布尔字段请填 true 或 false）" if expected is bool else "（例如 suggested_entities_max 应填一个正整数）"
            raise ComplianceConfigError(
                f"""配置文件 {source} 的字段 'compliance_gateway.{key}' 类型不对：
期望 {expected.__name__}，实际是 {type(value).__name__}。
请检查该字段填的值。{hint}"""
            )

    # L1 增强：obfuscation_mapping 必须是「字符串真名 -> 字符串占位符」，且占位符唯一；
    # 否则 obfuscate/restore 会因非 str 值或占位符重复而崩溃/还原错乱。
    obf_mapping = gw.get("obfuscation_mapping")
    if isinstance(obf_mapping, dict):
        for mkey, mval in obf_mapping.items():
            if not isinstance(mkey, str) or not isinstance(mval, str):
                raise ComplianceConfigError(
                    f"配置文件 {source} 的字段 'compliance_gateway.obfuscation_mapping' 中存在非字符串映射：{mkey!r}: {mval!r}。"
                    "该字段约定为「真名: 占位符」，两者都必须是字符串。请检查该项。"
                )
        placeholders = [v for v in obf_mapping.values() if isinstance(v, str)]
        if len(placeholders) != len(set(placeholders)):
            raise ComplianceConfigError(
                f"配置文件 {source} 的字段 'compliance_gateway.obfuscation_mapping' 中出现了重复的占位符。"
                "每个占位符只能对应一个真名，否则还原会错乱。请改为唯一占位符。"
            )

    return cfg


def load_compliance(path: str | None = None) -> dict:
    """加载并校验出网安全网关合规配置。

    path 缺省时读本模块自带的 compliance.yaml；
    传入 path 时读指定文件（支持相对/绝对路径或 pathlib.Path）。

    返回：
        解析后的 dict，含 'compliance_gateway' 段（含用户额外新增字段）。

    异常：
        ComplianceConfigError —— 文件缺失 / YAML 解析失败 / 字段缺失或类型不符；
        message 为人话（原因 + 下一步建议），可直接展示。
    """
    source = Path(path) if path is not None else DEFAULT_CONFIG_PATH

    if not source.is_file():
        raise ComplianceConfigError(
            f"""找不到出网安全网关配置文件：{source}
这通常是文件被移动或删除了。请从项目备份把它恢复回来，
或新建一个文件并把下面的默认内容贴进去：
{_DEFAULT_SNIPPET}"""
        )

    try:
        text = source.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise ComplianceConfigError(
            f"""配置文件 {source} 无法按 UTF-8 读取（文件编码不是 UTF-8）。
请用文本编辑器把该文件另存为 UTF-8 编码（Windows 记事本选「UTF-8」）后再重试。
具体提示：{exc}"""
        ) from exc

    try:
        cfg = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        detail = getattr(exc, "problem", None) or str(exc)
        raise ComplianceConfigError(
            f"""配置文件 {source} 不是合法的 YAML，解析失败。
解析器提示：{detail}
请检查缩进、冒号与引号是否配对；如果拿不准，直接用下面的默认示例替换本文件：
{_DEFAULT_SNIPPET}"""
        ) from exc

    return _validate(cfg, str(source))


__all__ = ["load_compliance", "ComplianceConfigError", "DEFAULT_CONFIG_PATH"]
