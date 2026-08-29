"""开源前自检：密钥泄露 / 本机绝对路径 / 个人信息 / 历史残留 / 禁入文件 / 必备文件。

对照 OPEN_SOURCE.md §五清单与 AGENTS.md「第三使用者」规则，扫描 git 跟踪文件。

用法：
  python scripts/verify_opensource.py            # 扫描当前 git 跟踪文件
  python scripts/verify_opensource.py --history  # 追加全量 git 历史密钥扫描
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

fails = 0
warns = 0


def ok(name: str) -> None:
    print(f"  [PASS] {name}")


def fail(name: str, detail: str = "") -> None:
    global fails
    fails += 1
    print(f"  [FAIL] {name}" + (f"\n         {detail}" if detail else ""))


def warn(name: str, detail: str = "") -> None:
    global warns
    warns += 1
    print(f"  [WARN] {name}" + (f"\n         {detail}" if detail else ""))


CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".json", ".yaml", ".yml",
    ".bat", ".cmd", ".ps1", ".sh", ".css", ".scss", ".html", ".htm", ".vue",
    ".toml", ".ini", ".cfg", ".sql",
}
DOC_EXTS = {".md", ".txt", ".rst"}
BIN_HINT_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".onnx", ".bin", ".pt",
    ".wav", ".mp3", ".zip", ".whl", ".exe", ".dll", ".woff", ".woff2", ".ttf",
}

SECRET_RULES = [
    ("私钥块", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("阿里云 AccessKey", re.compile(r"\bLTAI[A-Za-z0-9]{12,}")),
    ("AWS AccessKey", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub Token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Slack Token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("sk- 密钥", re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")),
]

PLACEHOLDER = re.compile(
    r"(?i)(x{4,}|\$\{|your[^,;\s]*|example|placeholder|<[^>]*>|\benviron|\bgetenv"
    r"|process\.env|\bdummy|changeme|xxxx)"
)
DOTENV_KEY = re.compile(r"^\s*(DEEPSEEK_API_KEY|DASHSCOPE_API_KEY|VOLC_\w+)\s*=\s*(\S.*)$")
GENERIC_ASSIGN = re.compile(
    r"(?i)[\"']?\b(api[_-]?key|secret|token|passwd|password)\b[\"']?\s*[:=]\s*[\"']?"
    r"([A-Za-z0-9][A-Za-z0-9_\-.]{15,})[\"']?"
)

WIN_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?!\.\.)")
WORKSPACE_ROOT = re.compile(r"[A-Za-z]:[\\\\/]04_Work\b")
UNIX_HOME = re.compile(r"(?<![\w])/(?:Users|home)/[\w.\-]+")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
EMAIL_EXCLUDE = re.compile(r"(?i)(example|noreply|@x\.|@2x|@gmail\.$|izs\.me)")
LEGACY_STRICT = re.compile(r"(?i)jarvis|贾维斯|picovoice|porcupine")
LEGACY_SOFT = re.compile(r"(?i)openwakeword")

FORBIDDEN_DIR_PARTS = [
    "logs/", "models/", "dev/", ".tmp/", "screenshots/", "node_modules/",
    "frontend/dist/", "desktop/release/", "desktop/runtime/", "__pycache__/",
    ".venv/", "venv/", ".uv-cache/", ".uv-tmp/", ".uv-python/", ".npm-cache/",
    ".gh-config/",
]
FORBIDDEN_EXTS = [".wav", ".mp3", ".tflite", ".pyc", ".log", ".tmp"]

REQUIRED_FILES = [
    "README.md", "README_EN.md", "LICENSE", "NOTICE", ".env.example",
    ".gitignore", "config.yaml", "requirements.txt",
    "requirements-local-asr.txt", "requirements-local-tts.txt",
]

BIG_FILE_BYTES = 5 * 1024 * 1024


def git(args: list[str]) -> str:
    r = subprocess.run(
        ["git"] + args, cwd=ROOT, capture_output=True,
        encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        fail("git 命令失败", " ".join(args) + "\n         " + r.stderr.strip())
        return ""
    return r.stdout


def tracked_files() -> list[str]:
    out = git(["ls-files", "-z"])
    return [p for p in out.split("\0") if p]


def mask(text: str, width: int = 24) -> str:
    text = text.strip()
    return text[:width] + "…" if len(text) > width else text


def scan_forbidden_paths(files: list[str]) -> None:
    print("=== 禁入文件（git 跟踪） ===")
    bad: list[str] = []
    for p in files:
        norm = p.replace("\\", "/")
        base = norm.rsplit("/", 1)[-1].lower()
        if base == ".env" or (base.startswith(".env.") and base != ".env.example"):
            bad.append(p)
            continue
        if any(part in norm + "/" for part in FORBIDDEN_DIR_PARTS):
            bad.append(p)
            continue
        if any(norm.lower().endswith(ext) for ext in FORBIDDEN_EXTS):
            bad.append(p)
    if bad:
        fail(f"{len(bad)} 个禁入文件被 git 跟踪", "示例: " + "; ".join(bad[:5]))
    else:
        ok("无禁入文件被跟踪（.env / logs / models / 产物等）")
    big = [p for p in files
           if (ROOT / p).exists() and (ROOT / p).stat().st_size > BIG_FILE_BYTES]
    if big:
        warn("疑似大文件进仓（>5MB，确认是否模型/二进制）", "; ".join(big[:5]))
    else:
        ok("无 >5MB 大文件进仓")


def scan_file_contents(files: list[str]) -> None:
    print("=== 内容扫描（密钥/路径/个人信息/残留） ===")
    secret_hits: dict[tuple[str, str], list[str]] = {}
    path_hits: dict[tuple[str, str], list[str]] = {}
    email_hits: dict[str, list[str]] = {}
    legacy_hits: dict[str, list[str]] = {}
    generic_hits: dict[str, list[str]] = {}

    for rel in files:
        fp = ROOT / rel
        if not fp.is_file():
            continue
        if rel.lower().endswith(tuple(BIN_HINT_EXTS)):
            continue
        if fp.stat().st_size > BIG_FILE_BYTES:
            continue
        raw = fp.read_bytes()
        if b"\x00" in raw[:8192]:
            continue
        text = raw.decode("utf-8", "replace")
        ext = fp.suffix.lower()
        is_code = ext in CODE_EXTS
        show = "doc" if (ext in DOC_EXTS or not ext) else "code"

        for lineno, line in enumerate(text.splitlines(), 1):
            for rule, pat in SECRET_RULES:
                if pat.search(line):
                    secret_hits.setdefault((rule, rel), []).append(f"L{lineno}: {mask(line)}")
            m = DOTENV_KEY.match(line)
            if m and not PLACEHOLDER.search(m.group(2)):
                secret_hits.setdefault(("明文 .env 密钥", rel), []).append(f"L{lineno}: {mask(m.group(1))}")
            g = GENERIC_ASSIGN.search(line)
            if g and not PLACEHOLDER.search(g.group(2)):
                generic_hits.setdefault(rel, []).append(f"L{lineno}: {g.group(1)}={mask(g.group(2), 12)}")
            for rule, pat, sev in (
                ("Win 绝对路径", WIN_PATH, "fail" if show == "code" else "warn"),
                ("本机工作区路径", WORKSPACE_ROOT, "fail"),
                ("Unix 用户路径", UNIX_HOME, "fail" if show == "code" else "warn"),
            ):
                hit = pat.search(line)
                if hit:
                    path_hits.setdefault((f"{rule}·{sev}", rel), []).append(f"L{lineno}: {mask(line, 40)}")
            e = EMAIL.search(line)
            if e and not EMAIL_EXCLUDE.search(e.group(0)):
                email_hits.setdefault(rel, []).append(f"L{lineno}: {mask(e.group(0), 30)}")
            legacy_strict = bool(LEGACY_STRICT.search(line))
            if legacy_strict or LEGACY_SOFT.search(line):
                legacy_hits.setdefault(rel, []).append((legacy_strict, f"L{lineno}: {mask(line, 40)}"))

    for (rule, rel), lines in secret_hits.items():
        fail(f"密钥泄露·{rule}", f"{rel}\n         " + "\n         ".join(lines[:3]))
    if not secret_hits:
        ok("未发现真实密钥（私钥/云 AK/GitHub/sk- 等）")
    for (rule_sev, rel), lines in path_hits.items():
        rule, sev = rule_sev.rsplit("·", 1)
        (fail if sev == "fail" else warn)(f"{rule}", f"{rel}\n         " + "\n         ".join(lines[:3]))
    if not path_hits:
        ok("未发现写死的本机绝对路径")
    for rel, lines in generic_hits.items():
        warn("疑似密钥字面量（人工确认）", f"{rel}\n         " + "\n         ".join(lines[:3]))
    for rel, items in legacy_hits.items():
        is_code = rel.lower().endswith(tuple(CODE_EXTS))
        hard = is_code and any(strict for strict, _ in items)
        (fail if hard else warn)(
            "历史残留命名（Jarvis/贾维斯/Picovoice/openWakeWord）",
            f"{rel}\n         " + "\n         ".join(t for _, t in items[:3]))
    if not legacy_hits:
        ok("无 Jarvis / Picovoice 残留")
    for rel, lines in email_hits.items():
        warn("疑似个人邮箱（人工确认）", f"{rel}\n         " + "\n         ".join(lines[:3]))
    if not email_hits:
        ok("未发现个人邮箱")


def scan_config_yaml() -> None:
    print("=== config.yaml 密钥字段 ===")
    fp = ROOT / "config.yaml"
    bad: list[str] = []
    key_line = re.compile(r"^\s*(api[_-]?key|apikey|token|secret|app_?id)\s*:\s*(.*)$", re.I)
    for lineno, line in enumerate(fp.read_text(encoding="utf-8").splitlines(), 1):
        m = key_line.match(line)
        if not m:
            continue
        val = m.group(2).strip().strip("'\"")
        if val and not PLACEHOLDER.search(val):
            bad.append(f"L{lineno}: {m.group(1)}")
    if bad:
        fail("config.yaml 存在非空密钥字段（应留空走 .env）", "; ".join(bad))
    else:
        ok("所有密钥字段为空（真实 key 只在本机 .env）")


def scan_files_presence() -> None:
    print("=== 必备文件与 .gitignore ===")
    missing = [f for f in REQUIRED_FILES if not (ROOT / f).is_file()]
    if missing:
        fail("缺少必备文件", "; ".join(missing))
    else:
        ok("必备文件齐全（README/LICENSE/NOTICE/.env.example 等）")
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8", errors="replace")
    need = [".env", "logs/", "models/"]
    lack = [n for n in need if n not in gi]
    if lack:
        fail(".gitignore 缺少关键条目", "; ".join(lack))
    else:
        ok(".gitignore 覆盖 .env / logs / models")
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8", errors="replace").lower()
    if "piper" in req:
        fail("requirements.txt 含 piper（GPL-3.0 传染，应留在 requirements-local-tts.txt）")
    else:
        ok("主依赖不含 piper（GPL 隔离，按需安装）")


def scan_history() -> None:
    print("=== git 全历史密钥扫描（--history） ===")
    proc = subprocess.Popen(
        ["git", "log", "-p", "-U0", "--no-color"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace",
    )
    assert proc.stdout is not None
    cur = ""
    hits: dict[str, set[str]] = {}
    for line in proc.stdout:
        m = re.match(r"^commit ([0-9a-f]{40})", line)
        if m:
            cur = m.group(1)[:10]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for rule, pat in SECRET_RULES:
            if pat.search(line):
                hits.setdefault(cur, set()).add(rule)
        d = DOTENV_KEY.match(line[1:])
        if d and not PLACEHOLDER.search(d.group(2)):
            hits.setdefault(cur, set()).add("明文 .env 密钥")
    proc.wait()
    if hits:
        items = list(hits.items())
        fail(f"{len(hits)} 个历史提交疑似含密钥", "; ".join(f"{c}({','.join(sorted(r))})" for c, r in items[:5]))
    else:
        ok("git 全历史未发现密钥")


def main() -> int:
    ap = argparse.ArgumentParser(description="开源前自检")
    ap.add_argument("--history", action="store_true", help="追加 git 全历史密钥扫描")
    args = ap.parse_args()

    print(f"=== 开源前自检（{ROOT.name}） ===")
    files = tracked_files()
    print(f"  git 跟踪文件: {len(files)} 个")
    if not files:
        return fails

    scan_forbidden_paths(files)
    scan_file_contents(files)
    scan_config_yaml()
    scan_files_presence()
    if args.history:
        scan_history()

    print("=== 汇总 ===")
    if fails == 0:
        print(f"  全部通过 ✅（{warns} 项警告需人工确认）")
    else:
        print(f"  {fails} 项失败 ❌，{warns} 项警告需人工确认")
    return fails


if __name__ == "__main__":
    sys.exit(main())
