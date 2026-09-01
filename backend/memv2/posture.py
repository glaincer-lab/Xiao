"""M2-A 四态姿态判定卡 · 规则引擎（backend/memv2/posture.py）。

根据 docs/specs/M2-heart.md §4.1（姿态判定卡）与 _M2-tasks/M2_CONTRACT.md
（PostureCard 接口、八态、关键纪律）实现首版**规则引擎**，不做 ML。

首版覆盖核心四态：陪伴态(companion) / 医护态(medical) / 应急态(emergency) /
同乐态(celebrate)，外加**默认朋友态(friend)兜底**；为顾问态(advisor)与
元对话态(metadialogue)预留 PostureCard 注册判定接口（默认不参与自动判定，
可经 ``PostureClassifier.activate`` 启用）。

契约（写死，见 M2_CONTRACT.md）：
    PostureCard                      # threshold/weight + enter_score/should_enter/should_exit/fallback
    PostureClassifier                # 内部维护各姿态 PostureCard，classify(text, context) -> str
    classify(text, context) -> str   # 返回八态之一

纪律（写死）：
    - 不确定 → 默认朋友态（误判比平淡伤）。
    - 用户否认 → 退回朋友态，**绝不二次试探**（被否认姿态被抑制，不再进入）。
    - 纯标准库，无 LLM、无第三方依赖；不硬耦合未实现的 M0 宏观四态底层 / 事件总线。

MIT。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# --------------------------------------------------------------------------- #
# 八态常量（写死，见 M2_CONTRACT.md）
# --------------------------------------------------------------------------- #
FRIEND = "friend"
COMPANION = "companion"
ADVISOR = "advisor"
MEDICAL = "medical"
EMERGENCY = "emergency"
CELEBRATE = "celebrate"
METADIALOGUE = "metadialogue"
# T9a：应对攻击/脏话/侮辱（走影子日志，默认不自动判定，见 DEFEND）
DEFEND = "defend"

#: 首版自动参与判定的核心四态（扫描顺序=安全优先：应急/医护 → 同乐 → 陪伴）
ACTIVE_CORE = (EMERGENCY, MEDICAL, CELEBRATE, COMPANION)

# --------------------------------------------------------------------------- #
# 信号词表（字面子串匹配；纯文本，不含音频叹气）
# --------------------------------------------------------------------------- #
EMOTION_WORDS = (
    "伤心", "难过", "好累", "累了", "孤独", "孤单", "低落", "心烦", "烦",
    "焦虑", "委屈", "哭", "想哭", "emo", "沮丧", "失落", "疲惫", "压抑",
    "无助", "不开心", "郁闷", "空虚", "心累", "撑不住",
)
SIGH_WORDS = ("唉", "哎", "好累", "叹气", "罢了", "心累", "好难")
SYMPTOM_WORDS = (
    "头痛", "头疼", "发烧", "发热", "头晕", "胃疼", "胃痛", "肚子疼", "咳嗽",
    "失眠", "睡不着", "鼻塞", "嗓子疼", "不舒服", "恶心", "呕吐", "腹泻",
    "酸痛", "乏力", "胸闷", "心悸",
)
EMERGENCY_WORDS = (
    "救命", "出事", "着火", "快救", "摔倒", "流血", "危险", "打120", "急救",
    "自杀", "想死", "不想活", "晕倒", "无法呼吸", "120", "报警", "求助",
    "叫救护车", "撑不住",
)
GOOD_NEWS_WORDS = (
    "考上", "升职", "涨薪", "加薪", "签约", "中奖", "中标", "过了", "通过",
    "成功", "好消息", "录取", "拿到offer", "拿到", "入职", "转正", "落地",
    "完工", "庆祝", "赢了",
)
POSITIVE_PATTERNS = (
    "太好了", "真棒", "开心", "高兴", "哈哈", "好耶", "恭喜", "太棒",
    "兴奋", "太爽", "不错", "棒极了",
)
DECISION_QUESTIONS = (
    "怎么办", "怎么选", "选哪个", "该不该", "要不要", "如何选", "怎么办好",
    "帮我选", "帮我看", "哪个好",
)

# 关系张力（T9a）：攻击/脏话/侮辱 → 应对类目。仅用于「识别并走影子日志」，不直接切换姿态。
# 三类场景各一张词表（进 extract_signals 的关系张力信号 + detect_attack_scene）。
VERBAL_ATTACK_WORDS = (
    "废物", "智障", "脑残", "傻逼", "白痴", "蠢货", "蠢蛋",
    "没用", "垃圾", "废柴", "蠢死了", "脑子有病", "脑子进水", "神经病",
)
PROFANITY_WORDS = (
    "他妈的", "妈的", "操你", "去你妈", "你妈逼", "滚蛋", "滚", "傻逼",
    "操", "草泥马", "卧槽", "恶心", "放屁",
)
DISCRIMINATION_WORDS = (
    "黑鬼", "白皮猪", "东亚病夫", "矮冬瓜", "穷鬼", "乡巴佬", "低贱", "贱人",
    "下贱", "土包子", "山炮", "病毒",
)

#: 深夜时段窗口：>=LATE_NIGHT_HOUR 或 <EARLY_MORNING_HOUR
LATE_NIGHT_HOUR = 22
EARLY_MORNING_HOUR = 6


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _to_number(value: Any) -> float:
    """把 bool/数值规范化成可参与加权的 float。bool True->1, False->0。"""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 1.0 if value else 0.0


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def _is_late_night(context: dict[str, Any]) -> bool:
    """深夜时段判定。context 可显式给 is_late_night，否则按 hour 窗口算。"""
    if context.get("is_late_night") is not None:
        return bool(context["is_late_night"])
    hour = context.get("hour")
    if hour is None:
        return False
    try:
        hour = int(hour)
    except (TypeError, ValueError):
        return False
    return hour >= LATE_NIGHT_HOUR or hour < EARLY_MORNING_HOUR


# --------------------------------------------------------------------------- #
# 信号抽取：text + context → 信号字典（各卡 enter_score 按 weight 取用）
# --------------------------------------------------------------------------- #
def extract_signals(text: str, context: dict[str, Any]) -> dict[str, Any]:
    """从文本与上下文抽取一组布尔/数值信号。

    返回的信号键与各 PostureCard.weight 的键对应；未用到的键权重为 0，
    不参与该卡组合分。
    """
    text = text or ""
    context = context or {}
    return {
        # 陪伴态
        "emotion_word": _contains_any(text, EMOTION_WORDS),
        "sigh_word": _contains_any(text, SIGH_WORDS),
        "late_night": _is_late_night(context),
        "session_context": bool(context.get("session_context")),
        # 医护态
        "symptom_word": _contains_any(text, SYMPTOM_WORDS),
        # 应急态
        "emergency_word": _contains_any(text, EMERGENCY_WORDS),
        # 同乐态
        "good_news_word": _contains_any(text, GOOD_NEWS_WORDS),
        "positive_pattern": _contains_any(text, POSITIVE_PATTERNS),
        # 顾问态
        "decision_question": _contains_any(text, DECISION_QUESTIONS),
        # 关系张力（T9a）：攻击/脏话/侮辱 → True；仅用于识别，不直接切换姿态。
        "relation_tension": bool(detect_attack_scene(text)),
        # 全局
        "user_denied": bool(context.get("user_denied")),
    }


# --------------------------------------------------------------------------- #
# 退出判定函数（每姿态一张，见 §4.1 退出信号列）
# --------------------------------------------------------------------------- #
def _companion_exit(session: dict[str, Any]) -> bool:
    """陪伴态退出：连续 3 轮 >20 字无情绪词，或用户说『好了』。"""
    if session.get("user_said_ok"):
        return True
    if session.get("consecutive_long_no_emotion", 0) >= 3:
        return True
    return False


def _event_clear_exit(session: dict[str, Any]) -> bool:
    """医护态/应急态退出：症状解除或转介后 / 事件解除。"""
    return bool(session.get("event_cleared") or session.get("referred"))


def _celebrate_exit(session: dict[str, Any]) -> bool:
    """同乐态退出：情绪自然回落。"""
    return bool(session.get("emotion_eased"))


def _advisor_exit(session: dict[str, Any]) -> bool:
    """顾问态退出：用户定夺。"""
    return bool(session.get("user_decided"))


def _metadialogue_exit(session: dict[str, Any]) -> bool:
    """元对话态退出：元对话完成。"""
    return bool(session.get("metadialogue_done"))


# --------------------------------------------------------------------------- #
# PostureCard：每姿态一张判定卡（接口写死，见 M2_CONTRACT.md）
# --------------------------------------------------------------------------- #
@dataclass
class PostureCard:
    """单姿态判定卡。

    - ``threshold``：组合分阈值，enter_score >= threshold 即应进入。
    - ``weight``：信号→权重（如 {"emotion_word": 0.5, "late_night": 0.3}）。
    - ``exit_check``：退出信号判定（无则恒 False，表示无退出条件）。
    - ``fallback_posture``：误判退路（如 user_deny -> friend）。
    """

    posture: str
    threshold: float = 0.0
    weight: dict[str, float] = field(default_factory=dict)
    exit_check: Callable[[dict], bool] | None = None
    fallback_posture: str = FRIEND

    def enter_score(self, signals: dict[str, Any]) -> float:
        """加权求和组合分。bool→1/0，数值→直接乘权重。"""
        total = 0.0
        for sig, val in signals.items():
            w = self.weight.get(sig, 0.0)
            if w == 0.0:
                continue
            total += w * _to_number(val)
        return total

    def should_enter(self, signals: dict[str, Any]) -> bool:
        """组合分超阈即应进入；threshold<=0 视为永不主动进入（friend 兜底卡）。"""
        if self.threshold <= 0:
            return False
        return self.enter_score(signals) >= self.threshold

    def should_exit(self, session: dict[str, Any]) -> bool:
        """退出信号。session 为当前会话状态（含各态退出所需字段）。"""
        if self.exit_check is None:
            return False
        return bool(self.exit_check(session or {}))

    def fallback(self) -> str:
        """误判退路（默认 friend）。"""
        return self.fallback_posture


# --------------------------------------------------------------------------- #
# PostureClassifier：内部维护各姿态 PostureCard，提供 classify(text, context)
# --------------------------------------------------------------------------- #
class PostureClassifier:
    """四态姿态判定卡规则引擎（+ 默认朋友态兜底 + 顾问/元对话留接口）。"""

    def __init__(self, cards: dict[str, PostureCard] | None = None) -> None:
        self.cards: dict[str, PostureCard] = cards or self._default_cards()
        # 首版自动参与判定的核心四态；顾问/元对话卡已注册但默认不参与。
        self.active: list[str] = [name for name in ACTIVE_CORE if name in self.cards]
        # 运行时状态：上次判定姿态（用于用户否认时抑制）+ 被否认抑制集。
        self._last_posture: str = FRIEND
        self._suppressed: set[str] = set()

    # -- 默认卡 ------------------------------------------------------------- #
    @staticmethod
    def _default_cards() -> dict[str, PostureCard]:
        return {
            COMPANION: PostureCard(
                COMPANION,
                threshold=0.8,
                weight={
                    "emotion_word": 0.5,
                    "sigh_word": 0.3,
                    "late_night": 0.4,
                    "session_context": 0.2,
                },
                exit_check=_companion_exit,
                fallback_posture=FRIEND,
            ),
            MEDICAL: PostureCard(
                MEDICAL,
                threshold=0.5,
                weight={"symptom_word": 0.6},
                exit_check=_event_clear_exit,
                fallback_posture=FRIEND,
            ),
            EMERGENCY: PostureCard(
                EMERGENCY,
                threshold=0.5,
                weight={"emergency_word": 0.6},
                exit_check=_event_clear_exit,
                fallback_posture=FRIEND,
            ),
            CELEBRATE: PostureCard(
                CELEBRATE,
                threshold=0.5,
                weight={"good_news_word": 0.5, "positive_pattern": 0.4},
                exit_check=_celebrate_exit,
                fallback_posture=FRIEND,
            ),
            # 留接口：顾问态（默认不自动判定，可 activate）
            ADVISOR: PostureCard(
                ADVISOR,
                threshold=0.5,
                weight={"decision_question": 0.6},
                exit_check=_advisor_exit,
                fallback_posture=FRIEND,
            ),
            # 留接口：元对话态（默认不自动判定，可 activate）
            METADIALOGUE: PostureCard(
                METADIALOGUE,
                threshold=1.0,
                weight={},
                exit_check=_metadialogue_exit,
                fallback_posture=FRIEND,
            ),
            # T9a：应对姿态卡（影子日志硬门：weight 仅在检测到关系张力时得分，
            # 但因 DEFEND 不在 ACTIVE_CORE，classify() 永不返回它——只识别、不切换）。
            DEFEND: PostureCard(
                DEFEND,
                threshold=0.6,
                weight={"relation_tension": 0.8},
                fallback_posture=FRIEND,
            ),
            # 默认兜底卡：threshold<=0，永不主动进入（friend 是 fallback）
            FRIEND: PostureCard(FRIEND, threshold=0.0, weight={}, fallback_posture=FRIEND),
        }

    # -- 配置 --------------------------------------------------------------- #
    def activate(self, *postures: str) -> None:
        """把（默认未启用的）姿态加入自动判定列表。用于启用顾问/元对话接口。"""
        for name in postures:
            if name in self.cards and name not in self.active:
                self.active.append(name)

    def deactivate(self, *postures: str) -> None:
        """把姿态移出自动判定列表（friend 恒为兜底，不受影响）。"""
        for name in postures:
            if name in self.active:
                self.active.remove(name)

    def reset_suppressed(self) -> None:
        """清空被否认抑制集（会话结束后调用）。"""
        self._suppressed.clear()
        self._last_posture = FRIEND

    # -- 判定 --------------------------------------------------------------- #
    def classify(self, text: str, context: dict[str, Any] | None) -> str:
        """返回八态之一的姿态名：四态命中→该态；否则默认朋友态。

        context 支持字段（均可选）：
            hour / is_late_night  : 深夜时段
            session_context       : 会话上下文标记（陪伴态加权信号之一）
            user_denied           : 用户否认本次姿态（→ 退朋友态 + 抑制该态，绝不二次试探）
        """
        context = context or {}
        signals = extract_signals(text, context)

        # 用户否认 → 退回朋友态，绝不二次试探：把上一次判定的姿态加入抑制集。
        if signals["user_denied"]:
            if self._last_posture not in (FRIEND, ""):
                self._suppressed.add(self._last_posture)
            self._last_posture = FRIEND
            return FRIEND

        # 按优先级扫描 active 卡：应急/医护（安全优先）→ 同乐 → 陪伴 → 顾问。
        for name in self.active:
            if name in self._suppressed:
                continue
            card = self.cards.get(name)
            if card is not None and card.should_enter(signals):
                self._last_posture = name
                return name

        # 不确定 → 默认朋友态（最关键纪律）。
        self._last_posture = FRIEND
        return FRIEND

    # -- 便捷查询 ----------------------------------------------------------- #
    def current(self) -> str:
        """最近一次判定结果（shadow/调试用）。"""
        return self._last_posture

    def suppressed(self) -> set[str]:
        """当前被否认抑制（不二次试探）的姿态集。"""
        return set(self._suppressed)


# --------------------------------------------------------------------------- #
# T9a：关系张力场景识别（attack / profanity / discrimination；无命中返回 None）
# --------------------------------------------------------------------------- #
ATTACK_SCENES: dict[str, tuple[str, ...]] = {
    "verbal": VERBAL_ATTACK_WORDS,
    "profanity": PROFANITY_WORDS,
    "discrimination": DISCRIMINATION_WORDS,
}


def detect_attack_scene(text: str) -> str | None:
    """识别攻击/脏话/侮辱所属场景类目。

    - ``"verbal"``         ：言语攻击/贬低（如『你个废物』）
    - ``"profanity"``      ：脏话/辱骂（如『他妈的』『滚』）
    - ``"discrimination"`` ：歧视/侮辱（如『东亚病夫』『穷鬼』）
    - ``None``             ：无关系张力

    优先级为 discrimination > profanity > verbal（歧视/脏话通常烈度更高，优先归目）。
    """
    text = text or ""
    for scene in ("discrimination", "profanity", "verbal"):
        if _contains_any(text, ATTACK_SCENES[scene]):
            return scene
    return None


# --------------------------------------------------------------------------- #
# 模块级便捷函数
# --------------------------------------------------------------------------- #
_DEFAULT_CLASSIFIER: PostureClassifier | None = None


def classify(text: str, context: dict[str, Any] | None) -> str:
    """便捷：使用默认 PostureClassifier 实例判定姿态。"""
    global _DEFAULT_CLASSIFIER
    if _DEFAULT_CLASSIFIER is None:
        _DEFAULT_CLASSIFIER = PostureClassifier()
    return _DEFAULT_CLASSIFIER.classify(text, context)
