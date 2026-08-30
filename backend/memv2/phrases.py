"""backend.memv2.phrases —— M2 话术库（YAML 加载 + 按姿态/情绪/亲密度选变体）。

接口契约（写死，见 M2-D.phrases-shadow.md / M2-heart §5）：

    load_phrases(directory=None) -> list[dict]   # 读取 prompts/ 下全部 *.yaml 话术
    pick(stance, emotion, intimacy) -> str        # 按姿态×情绪×亲密度选一条话术模板
    all_ban_words(phrases=None) -> list[str]      # 全量收拢所有 ban_words，供屏蔽扫描

加载策略（第三使用者视角）：
- 优先用 PyYAML（requirements.txt 已声明 ``PyYAML>=6.0``）；
- 若环境没有 PyYAML，回退到本模块内置的最小 YAML 子集解析器（仅覆盖话术库
  schema：顶层「映射列表 + 内联 [..] 列表 + 引号字符串 + 注释行」），保证
  ``load_phrases`` 在任何环境下都能返回相同契约。

话术条目 schema（一元话术 = 一条 dict）：
    id           唯一 ID（str）
    stance       八态之一（str）
    emotion      any|low|high（str）
    intimacy_range  [min, max]（list[int]）
    skeleton     骨架标记（list[str]）
    template     说话模板，含 {var} 占位符（str）
    ban_words    禁用词（list[str]）
    variables    模板变量名（list[str]）

仅标准库 + 可选 PyYAML；MIT。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

# 项目根 = backend/memv2/ 上溯三级（不依赖 backend.config，避免加载 .env/config.yaml）
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# 无匹配时的兜底话术（请勿包含「记岔了」等断言，避免遮蔽必收组检查）
DEFAULT_PHRASE = "嗯，我在，慢慢说。"

# 话术条目的合法键（用于 load 后校验与最小解析器白名单）
_PHRASE_KEYS = frozenset(
    {"id", "stance", "emotion", "intimacy_range", "skeleton", "template", "ban_words", "variables"}
)


# --------------------------------------------------------------------------- #
# 加载
# --------------------------------------------------------------------------- #
def _yaml_safe_load(text: str) -> list[dict[str, Any]]:
    """用 PyYAML 解析；环境缺 yaml 时抛 ImportError，调用方回退到最小解析器。"""
    import yaml  # noqa: WPS433（延迟导入：保证纯标准库环境也能 import 本模块）

    data = yaml.safe_load(text)
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError(f"话术库 YAML 顶层必须是列表，收到 {type(data).__name__}")
    return list(data)


def _load_file(path: Path) -> list[dict[str, Any]]:
    """读取一个 YAML 文件为话术列表；按需 PyYAML 或最小解析器。"""
    text = path.read_text(encoding="utf-8")
    try:
        raw = _yaml_safe_load(text)
        parser_note = "pyyaml"
    except ImportError:
        raw = _parse_minimal_yaml(text)
        parser_note = "builtin"
    return [_coerce_phrase(item, path, parser_note) for item in raw]


def _coerce_phrase(item: Any, path: Path, source: str) -> dict[str, Any]:
    """把一条原始 YAML 映射规整为契约话术 dict，并简单校验。"""
    if not isinstance(item, dict):
        raise ValueError(f"{path.name}: 话术条目必须是映射，收到 {item}")
    phrase = {
        "id": str(item.get("id", "")).strip(),
        "stance": str(item.get("stance", "")).strip(),
        "emotion": str(item.get("emotion", "any")).strip().lower() or "any",
        "intimacy_range": _as_int_range(item.get("intimacy_range")),
        "skeleton": _as_str_list(item.get("skeleton")),
        "template": str(item.get("template", "")),
        "ban_words": _as_str_list(item.get("ban_words")),
        "variables": _as_str_list(item.get("variables")),
        "_source": path.name,       # 仅用于调试/校验，不属于契约字段
        "_parser": source,
    }
    if not phrase["id"]:
        raise ValueError(f"{path.name}: 话术条目缺少 id")
    if not phrase["stance"]:
        raise ValueError(f"{path.name}: id={phrase['id']} 缺少 stance")
    if not phrase["template"]:
        raise ValueError(f"{path.name}: id={phrase['id']} 缺少 template")
    return phrase


def load_phrases(directory: str | Path | None = None) -> list[dict[str, Any]]:
    """读取目录下全部 ``*.yaml`` 话术并合并为列表（按文件名排序，结果确定）。

    ``directory`` 缺省为项目根的 ``prompts/``。
    """
    base = Path(directory) if directory is not None else PROMPTS_DIR
    if not base.is_dir():
        raise FileNotFoundError(f"话术库目录不存在: {base}")
    phrases: list[dict[str, Any]] = []
    for path in sorted(base.glob("*.yaml")):
        phrases.extend(_load_file(path))
    return phrases


def all_ban_words(phrases: Iterable[dict[str, Any]] | None = None) -> list[str]:
    """全量收拢所有话术的 ban_words，去重、去空并排序，供屏蔽词扫描。"""
    pool = phrases if phrases is not None else load_phrases()
    seen: set[str] = set()
    for p in pool:
        for w in p.get("ban_words", []):
            w = str(w).strip()
            if w:
                seen.add(w)
    return sorted(seen)


# --------------------------------------------------------------------------- #
# 选变体
# --------------------------------------------------------------------------- #
def pick(stance: str, emotion: str, intimacy: float, phrases: Iterable[dict[str, Any]] | None = None) -> str:
    """按姿态×情绪×亲密度选一条话术模板并返回。

    - ``stance``   ：八态之一，须与条目 ``stance`` 完全一致（严格匹配）。
    - ``emotion``  ：``any`` | ``low`` | ``high``。条目 ``emotion`` 为 ``any``
                     时任意情绪都命中；否则须与传入值一致。
    - ``intimacy`` ：0-100，命中条目 ``intimacy_range`` 区间。
    - 命中多条时，优先「情绪精确匹配」的变体，再按文件顺序取首个（确定性）。
    - 无任何命中 → 返回 ``DEFAULT_PHRASE``（兜底，保证永远有可说的一句）。
    """
    pool = phrases if phrases is not None else load_phrases()
    emotion = (emotion or "any").strip().lower() or "any"
    try:
        intimacy = float(intimacy)
    except (TypeError, ValueError):
        intimacy = _DEFAULT_INTIMACY

    cand = [
        p for p in pool
        if p.get("stance") == stance
        and p.get("emotion") in ("any", emotion)
        and _in_intimacy(p, intimacy)
    ]
    if not cand:
        return DEFAULT_PHRASE

    exact = [p for p in cand if p.get("emotion") == emotion]
    chosen = exact[0] if exact else cand[0]
    return str(chosen["template"])


_DEFAULT_INTIMACY = 50.0
_DEFAULT_INTIMACY_RANGE = (0, 100)


def _in_intimacy(phrase: dict[str, Any], intimacy: float) -> bool:
    rng = phrase.get("intimacy_range")
    if not rng:
        return True
    try:
        lo, hi = float(rng[0]), float(rng[1])
    except (TypeError, ValueError, IndexError):
        return True
    return lo <= intimacy <= hi


# --------------------------------------------------------------------------- #
# 最小 YAML 子集解析器（仅覆盖话术库 schema，作为无 PyYAML 时的回退）
# --------------------------------------------------------------------------- #
def _parse_minimal_yaml(text: str) -> list[dict[str, Any]]:
    """解析本话术库使用的 YAML 子集：顶层为 ``- key: value`` 列表项列表。"""
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\n").rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0 and stripped.startswith("- "):
            if current is not None:
                items.append(current)
            body = stripped[2:].strip()
            if body.startswith("#"):
                current = None
                continue
            current = {}
            if ":" in body:
                key, val = body.split(":", 1)
                current[key.strip()] = _parse_scalar(val.strip())
            continue
        # 续行：归属于当前条目的 "key: value"
        if current is None or ":" not in stripped:
            continue
        key, val = stripped.split(":", 1)
        current[key.strip()] = _parse_scalar(val.strip())
    if current is not None:
        items.append(current)
    return items


def _parse_scalar(s: str) -> Any:
    s = s.strip()
    if not s:
        return ""
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_parse_scalar(x) for x in _split_inline(inner)] if inner else []
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if s in ("null", "~", "None"):
        return None
    if re.fullmatch(r"[-+]?\d+", s):
        return int(s)
    if re.fullmatch(r"[-+]?\d+\.\d+", s):
        return float(s)
    return s


def _split_inline(s: str) -> list[str]:
    """按顶层逗号切分内联列表，逗号在引号内时不被切开。"""
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in s:
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


# --------------------------------------------------------------------------- #
# 类型规整辅助
# --------------------------------------------------------------------------- #
def _as_int_range(raw: Any) -> list[int]:
    if raw is None:
        return [_DEFAULT_INTIMACY_RANGE[0], _DEFAULT_INTIMACY_RANGE[1]]
    vals = raw if isinstance(raw, (list, tuple)) else [raw]
    out: list[int] = []
    for v in vals:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            out.append(_DEFAULT_INTIMACY_RANGE[0])
    if len(out) < 2:
        out.append(_DEFAULT_INTIMACY_RANGE[1])
    return out[:2]


def _as_str_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw]
    return [str(raw).strip()]
