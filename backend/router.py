"""路由层：决定一句话走「聊天」还是「干活(DSH)」。

- mode=auto：关键词命中走 dsh，否则走 chat
- mode=chat / mode=dsh：手动强制
- 每次决策写路由日志（JSONL），便于事后分析误判、补充规则
- 日志脱敏：text 字段经 _sanitize 遮蔽密码/密钥/身份证/自伤类关键词及其后续值
- 容量轮转：按天轮转，达字节/行数上限即归档 routes-YYYYMMDD.json，保留最近 N 个
"""
from __future__ import annotations

import json
import os
import re
import time

from backend.config import ROOT, config

# 兜底敏感词表：compliance.yaml 不可用时仍遮蔽常见敏感词（本地脱敏，绝不明文落盘）。
_FALLBACK_KEYWORDS: tuple[str, ...] = (
    "密码", "密钥", "身份证", "口令", "token", "secret", "password",
    "api_key", "api-key", "自杀", "不想活了",
)


def _positive_int(value: object, default: int) -> int:
    """把配置值安全转为正整数，非法/非正值回退默认。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _sanitize(text: str) -> str:
    """本地脱敏：遮蔽敏感词及其后续值（直到中文/英文标点或行尾）为 [REDACTED]。

    复用 gateway.load_config 的 local_only_keywords 黑词表，配置不可用时以内置
    _FALLBACK_KEYWORDS 兜底；任何异常静默吞掉，绝不阻断路由决策。
    """
    if not text or not isinstance(text, str):
        return text
    result = str(text)
    keywords = list(_FALLBACK_KEYWORDS)
    try:
        from backend.gateway.load_config import load_compliance

        c = load_compliance()
        gw_section = c.get("compliance_gateway", c) if isinstance(c, dict) else {}
        extra = gw_section.get("local_only_keywords", []) or []
        for k in extra:
            kk = str(k).strip()
            if kk and kk not in keywords:
                keywords.append(kk)
    except Exception:  # noqa: BLE001
        pass  # 配置不可用则仅靠内置兜底词表。
    joined = "|".join(re.escape(k) for k in keywords if k)
    if not joined:
        return result
    return re.sub(
        r"(" + joined + r")[^，。；！？,.;!?\n]*",
        "[REDACTED]",
        result,
    )


class Router:
    def __init__(self) -> None:
        self._mode = str(config.get("router.mode", "auto")).lower()
        self._keywords = [str(k) for k in (config.get("router.dsh_keywords", []) or []) if k]
        self._log_path = os.path.join(ROOT, str(config.get("router.log_path", "logs/routes.jsonl")))
        self._log_max_bytes = _positive_int(config.get("router.log_max_bytes"), 1048576)
        self._log_max_lines = _positive_int(config.get("router.log_max_lines"), 10000)
        self._log_keep_days = _positive_int(config.get("router.log_keep_days"), 5)

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        mode = str(mode).lower()
        if mode in ("auto", "chat", "dsh"):
            self._mode = mode

    def reload_keywords(self) -> None:
        """保存配置后重载关键词（软配置热加载）。"""
        self._keywords = [str(k) for k in (config.get("router.dsh_keywords", []) or []) if k]

    def route(self, text: str) -> str:
        if self._mode == "chat":
            decision = "chat"
        elif self._mode == "dsh":
            decision = "dsh"
        else:
            decision = "dsh" if self._hit(text) else "chat"
        self._log(text, decision)
        return decision

    def _hit(self, text: str) -> bool:
        t = text.lower()
        return any(k.lower() in t for k in self._keywords)

    def _log(self, text: str, decision: str) -> None:
        try:
            safe_text = _sanitize(text)
            self._rotate_if_needed()
            os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(
                    {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "mode": self._mode,
                     "decision": decision,
                     "text": safe_text},
                    ensure_ascii=False,
                ) + "\n")
        except Exception:  # noqa: BLE001
            pass  # 脱敏/轮转/写盘任何异常都静默吞掉，绝不让路由主流程中断。

    def _rotate_if_needed(self) -> None:
        """当前活跃日志达字节/行数上限即轮转归档（先到者触发）。"""
        try:
            if not os.path.isfile(self._log_path):
                return
            if os.path.getsize(self._log_path) >= self._log_max_bytes:
                self._rotate_now()
                return
            with open(self._log_path, "r", encoding="utf-8") as f:
                line_count = sum(1 for _ in f)
            if line_count >= self._log_max_lines:
                self._rotate_now()
        except Exception:  # noqa: BLE001
            pass

    def _rotate_now(self) -> None:
        """把当前 routes.jsonl 原子重命名为 routes-YYYYMMDD.json（同日重名加序号），并裁剪旧文件。"""
        try:
            if not os.path.isfile(self._log_path):
                return
            d = os.path.dirname(self._log_path)
            day = time.strftime("%Y%m%d")
            target = os.path.join(d, f"routes-{day}.json")
            n = 1
            while os.path.exists(target):
                target = os.path.join(d, f"routes-{day}-{n}.json")
                n += 1
            os.replace(self._log_path, target)
            self._prune_old()
        except Exception:  # noqa: BLE001
            pass

    def _prune_old(self) -> None:
        """历史天文件总量不超过 _log_keep_days，超出删除最旧的。"""
        try:
            d = os.path.dirname(self._log_path)
            stem = os.path.splitext(os.path.basename(self._log_path))[0]
            keep = max(1, self._log_keep_days)
            files = sorted(
                (os.path.join(d, name) for name in os.listdir(d)
                 if name.startswith(stem + "-") and name.endswith(".json")),
                key=os.path.getmtime,
            )
            while len(files) > keep:
                oldest = files.pop(0)
                try:
                    os.remove(oldest)
                except OSError:
                    pass
        except Exception:  # noqa: BLE001
            pass
