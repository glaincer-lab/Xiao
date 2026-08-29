"""L0 规则引擎：触发词 → 内置工具，无 LLM、无 API key 也能用。

匹配发生在 Pipeline._dispatch 的 router.route 之前：
- 命中即执行对应工具并播报结果；
- 提取不到必要参数（如「提醒我」没说几分钟）时回落到对话/DSH，不硬拦截。
词表可在 config.yaml 的 router.rules.keywords 按规则覆盖，reload_soft 热生效。
"""
from __future__ import annotations

import re

from backend.config import config

# 与 Pipeline._normalize 一致：去标点、转小写，只留中英文与数字
_PUNCT_STRIP = " \t，。,.、!！？?；;：:~～·"

# 常用应用/网址别名（语音口语 → open_app 的 target）
APP_ALIASES = {
    "记事本": "notepad",
    "计算器": "calc",
    "画图": "mspaint",
    "画板": "mspaint",
    "任务管理器": "taskmgr",
    "资源管理器": "explorer",
    "文件管理器": "explorer",
    "控制面板": "control",
    "设置": "ms-settings:",
    "浏览器": "https://www.bing.com",
    "命令行": "cmd",
    "终端": "cmd",
    "截图工具": "snippingtool",
}

_CURRENCIES = {
    "美元": "USD",
    "美金": "USD",
    "日元": "JPY",
    "日币": "JPY",
    "欧元": "EUR",
    "港币": "HKD",
    "港元": "HKD",
    "英镑": "GBP",
    "韩元": "KRW",
    "卢布": "RUB",
    "澳元": "AUD",
    "加元": "CAD",
    "新加坡元": "SGD",
    "新币": "SGD",
    "泰铢": "THB",
    "卢比": "INR",
}

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

_DURATION_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?|[零一二两三四五六七八九十]+)\s*(个?小时|分钟?|秒)")


def _normalize(s: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s).lower()


def _cn_to_int(s: str) -> int | None:
    s = s.strip()
    if not s:
        return None
    if re.fullmatch(r"[0-9]+(\.[0-9]+)?", s):
        return int(float(s))
    if "十" in s:
        left, _, right = s.partition("十")
        if (left and left not in _CN_DIGITS) or (right and right not in _CN_DIGITS):
            return None
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if all(ch in _CN_DIGITS for ch in s):
        value = 0
        for ch in s:
            value = value * 10 + _CN_DIGITS[ch]
        return value
    return None


def _strip_edges(s: str, lead: tuple[str, ...] = (), tail: tuple[str, ...] = ()) -> str:
    s = s.strip().strip(_PUNCT_STRIP)
    changed = True
    while changed and s:
        changed = False
        for w in lead:
            if s.startswith(w):
                s = s[len(w):].strip().strip(_PUNCT_STRIP)
                changed = True
        for w in tail:
            if s.endswith(w):
                s = s[: -len(w)].strip().strip(_PUNCT_STRIP)
                changed = True
    return s


def _split_around(text: str, kw: str) -> tuple[str, str]:
    """优先在原文找关键词位置（保留 URL 等标点）；找不到再退回归一化文本。"""
    idx = text.find(kw)
    if idx < 0:
        t = _normalize(text)
        idx = t.find(_normalize(kw))
        if idx < 0:
            return "", ""
        return t[:idx], t[idx + len(_normalize(kw)):]
    return text[:idx], text[idx + len(kw):]


def _build_rest(text: str, kw: str) -> dict | None:
    before, after = _split_around(text, kw)
    lead = ("帮我", "帮忙", "麻烦", "请", "一下", "个")
    tail = ("吧", "呗", "啊", "呀", "呢", "一下")
    target = _strip_edges(after, lead=lead, tail=tail) or _strip_edges(before, lead=lead, tail=tail)
    if not target:
        return None
    return {"target": APP_ALIASES.get(target, target)}


def _build_before(text: str, kw: str) -> dict | None:
    before, _ = _split_around(text, kw)
    city = _strip_edges(
        before,
        lead=("帮我", "帮忙", "麻烦", "请", "查一下", "查查", "看看", "看下", "查", "现在", "当前", "今天", "今日", "明天", "后天"),
        tail=("的", "呢", "吧", "啊", "呀"),
    )
    return {"city": city}


def _build_copy(text: str, kw: str) -> dict | None:
    before, after = _split_around(text, kw)
    lead = ("帮我", "帮忙", "麻烦", "请", "把", "将", "一下", "个")
    tail = ("一下", "吧", "呗", "啊", "呀", "呢")
    content = _strip_edges(before, lead=lead, tail=tail) or _strip_edges(after, lead=lead, tail=tail)
    if not content:
        return None
    return {"action": "copy", "text": content}


def _build_duration(text: str, kw: str) -> dict | None:
    m = _DURATION_RE.search(text)
    if m is None:
        return None
    value = _cn_to_int(m.group(1))
    if value is None or value <= 0:
        return None
    unit = m.group(2)
    if "小时" in unit:
        seconds = value * 3600
    elif "秒" in unit:
        seconds = value
    else:
        seconds = value * 60
    message = text.replace(m.group(0), " ", 1).replace(kw, " ", 1)
    message = _strip_edges(
        message,
        lead=("的", "之后", "以后", "后", "再", "提醒我", "提醒", "叫我"),
        tail=("吧", "呗", "啊", "呀", "呢", "一下"),
    )
    return {"seconds": float(seconds), "message": message or "时间到了"}


def _build_currency(text: str, kw: str) -> dict | None:
    for name, code in _CURRENCIES.items():
        if name in text:
            return {"base": code}
    return {"base": "USD"}


# 有序 dict：排在前面的规则先匹配；同规则内关键词靠前的先命中
_RULES: dict[str, dict] = {
    "open_app": {"tool": "open_app", "extract": "rest", "keywords": ["打开", "启动"]},
    "volume_up": {"tool": "volume", "kwargs": {"action": "up"}, "keywords": ["音量调大", "调大音量", "音量大点", "大点声"]},
    "volume_down": {"tool": "volume", "kwargs": {"action": "down"}, "keywords": ["音量调小", "调小音量", "音量小点", "小点声"]},
    "volume_mute": {"tool": "volume", "kwargs": {"action": "mute"}, "keywords": ["取消静音", "解除静音", "静音"]},
    "screenshot": {"tool": "screenshot", "keywords": ["截个图", "截图", "截屏"]},
    "lock_screen": {"tool": "lock_screen", "speak_first": True, "reply": "好的，马上锁屏。", "keywords": ["锁屏", "锁定电脑"]},
    "sleep_pc": {"tool": "sleep_pc", "speak_first": True, "reply": "好的，电脑要休息了，晚安。", "keywords": ["睡眠模式", "休眠", "睡眠"]},
    "media_toggle": {"tool": "media", "kwargs": {"action": "play_pause"}, "keywords": ["暂停播放", "继续播放", "播放音乐", "暂停"]},
    "media_stop": {"tool": "media", "kwargs": {"action": "stop"}, "keywords": ["停止播放"]},
    "media_next": {"tool": "media", "kwargs": {"action": "next"}, "keywords": ["下一首"]},
    "media_prev": {"tool": "media", "kwargs": {"action": "prev"}, "keywords": ["上一首"]},
    "clipboard_read": {"tool": "clipboard", "kwargs": {"action": "read"}, "keywords": ["朗读剪贴板", "念一下剪贴板", "读一下剪贴板"]},
    "clipboard_paste": {"tool": "clipboard", "kwargs": {"action": "paste"}, "keywords": ["粘贴"]},
    "clipboard_copy": {"tool": "clipboard", "extract": "copy", "keywords": ["复制到剪贴板", "复制一下", "复制"]},
    "time_now": {"tool": "time_now", "keywords": ["几点", "现在时间", "今天几号", "几号", "今天日期", "星期几", "今天星期"]},
    "weather": {"tool": "weather", "extract": "before", "keywords": ["天气"]},
    "exchange_rate": {"tool": "exchange_rate", "extract": "currency", "keywords": ["汇率", "兑换"]},
    "reminder": {"tool": "reminder", "extract": "duration", "keywords": ["提醒我", "倒计时", "定时"]},
}

_BUILDERS = {
    "rest": _build_rest,
    "before": _build_before,
    "copy": _build_copy,
    "duration": _build_duration,
    "currency": _build_currency,
}


class RuleEngine:
    """关键词 → 工具调用；词表可被 config.yaml 的 router.rules.keywords 覆盖。"""

    def __init__(self) -> None:
        self._enabled = True
        self._keywords: dict[str, list[str]] = {}
        self.reload()

    def reload(self) -> None:
        self._enabled = bool(config.get("router.rules.enabled", True))
        override = config.get("router.rules.keywords", {}) or {}
        merged: dict[str, list[str]] = {}
        for rule_id, spec in _RULES.items():
            words = override.get(rule_id) or spec["keywords"]
            merged[rule_id] = [str(k).strip() for k in (words or []) if str(k).strip()]
        self._keywords = merged

    def match(self, text: str) -> dict | None:
        """返回 {id, tool, kwargs, speak_first, reply}；未命中返回 None。"""
        if not self._enabled or not text:
            return None
        t = _normalize(text)
        if not t:
            return None
        for rule_id, keywords in self._keywords.items():
            spec = _RULES[rule_id]
            for kw in keywords:
                nk = _normalize(kw)
                if not nk or nk not in t:
                    continue
                builder = _BUILDERS.get(str(spec.get("extract", "")))
                kwargs = dict(spec.get("kwargs") or {})
                if builder is not None:
                    extra = builder(text, kw)
                    if extra is None:  # 提取不到参数：试本规则其它说法，都不行就回落对话
                        continue
                    kwargs.update(extra)
                return {
                    "id": rule_id,
                    "tool": spec["tool"],
                    "kwargs": kwargs,
                    "speak_first": bool(spec.get("speak_first")),
                    "reply": spec.get("reply"),
                }
        return None
