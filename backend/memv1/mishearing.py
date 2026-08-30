"""M1-D 听错入口分级（纯规则引擎，无 LLM）。

规格本源：``docs/specs/M1-memory.md`` §4.3（听错入口分级高/中/低）+
``_M1-tasks/MEMV1_CONTRACT.md`` 的 ``MemEntry.confirmed`` 字段。

目标：在记忆写入前判断风险等级，**高风险必须复述确认后才入库（confirmed=true）**。

判定（写死，来自 §4.3）：
- **高**：记忆写入 / 数字·日期·人名 / 不可逆指令，或检测到听错信号异常
        → 即时复述确认，确认后才入库 + ``confirmed=true``。
- **中**：可逆任务 → 直接执行（执行结果即反馈）。
- **低**：闲聊 → 不反馈，自然滑过。

检测信号（§4.3）：时长·字数比异常 / VAD 切碎 / 语义残句 / 数字·日期·人名高危词。
**ASR 置信度字段在官方文档核实前不依赖**——本模块不读取任何 ASR 置信度字段。

反馈形态（§4.3）：复述确认 / 反问补充 / 无声重听。**音频零留存**：本模块
处理听错入口时**永不保留任何原始音频**（嘈杂场景走"无声重听"，星云示态，
不开口说"啊？"）。

仅标准库（``re``）。无任何文件/音频 IO。
"""
from __future__ import annotations

import re

RISK_HIGH = "high"
RISK_MEDIUM = "medium"
RISK_LOW = "low"

# 音频零留存：听错入口处理绝不保留任何原始音频（§4.3 写死）
AUDIO_RETENTION_FORBIDDEN = True

# ---------------------------------------------------------------------------
# 词表 / 规则（规则引擎全部在此，均为写死阈值，纯标准库）
# ---------------------------------------------------------------------------

# 记忆写入引导（声明性偏好/事实，需复述确认）
_MEMORY_WRITE_HINTS = (
    "记住", "记一下", "记着", "记得", "别忘了", "别忘记", "不要忘", "帮我记",
    "以后要", "以后不", "以后都", "从不", "再也不", "我只", "我希望你记住",
    "请记住", "请注意", "我喜欢", "我讨厌", "我偏爱", "我的习惯", "我的生日",
    "我的最爱", "我永远",
)

# 不可逆指令（删除/清空/永久生效，需复述确认）
_IRREVERSIBLE_HINTS = (
    "删除", "删掉", "清空", "彻底", "永久", "注销", "取消订阅", "退出",
    "卸载", "取消关注", "屏蔽", "拉黑", "你再也别", "不许",
)

# 可逆任务动词（执行结果即反馈，不改写长期记忆）
_TASK_HINTS = (
    "帮我", "请", "定个", "设置", "设定", "调", "播放", "打开", "关闭",
    "关掉", "开一下", "查一下", "查查", "搜索", "翻译", "提醒我", "安排",
    "预约", "下单", "买", "点一", "点个", "点餐", "点外卖", "来", "给我",
    "拿", "暂停", "继续", "倒计时", "计时", "叫", "呼叫", "搜一下", "搜搜",
    "找一下", "看看", "发个", "发送", "播报",
)

# 称谓 / 人名（易听错，需复述确认）
_HONORIFICS = (
    "老师", "医生", "律师", "教授", "同学", "同事", "老板", "经理",
    "爸爸", "妈妈", "爸", "妈", "哥", "姐", "弟", "妹", "叔叔", "阿姨",
    "爷爷", "奶奶", "先生", "女士", "小姐", "女朋友", "男朋友", "老婆",
    "老公", "女儿", "儿子",
)

# 复述确认需剥离的引导前缀（保留核心信息）
_CONFIRM_STRIP_PREFIXES = (
    "请记住", "记住", "记一下", "记着", "请注意", "别忘了", "别忘记",
    "帮我记", "以后要", "以后不", "以后都", "我希望你记住", "帮我记住",
)

_CN_NUM = "零一二两三四五六七八九十百千万"

# 具体日期/时间/数量（泛指"今天/明天/上午"这类时间副词不算，闲聊常见）
_DATE_PATTERN = re.compile(
    rf"[{_CN_NUM}0-9]+\s*(?:月|号|日|年)"
    r"|周[一二三四五六日天]"
    r"|[上中下]?午"
    r"|[{_CN_NUM}0-9]+\s*点"
    r"|[{_CN_NUM}0-9]+\s*时(?:\s*[{_CN_NUM}0-9]+\s*分)?"
    r"|[{_CN_NUM}0-9]+\s*[:：]\s*[{_CN_NUM}0-9]+"
)
_NUM_PATTERN = re.compile(rf"[{_CN_NUM}0-9]+")

# 常见姓氏 + 称谓（如「张总 / 王老师 / 李经理」）
_NAME_PATTERN = re.compile(
    r"[李王张刘陈杨赵黄周吴徐孙胡朱高林何郭马罗梁宋郑谢韩唐冯于董萧"
    r"程曹袁邓许傅沈曾彭吕苏卢蒋蔡贾丁魏薛叶阎余潘杜戴夏钟汪田任姜"
    r"范方石姚谭廖邹熊金陆郝孔白崔康毛邱秦江史顾侯邵孟龙万段雷钱汤"
    r"尹黎易常武乔贺赖龚文]_?(?:总|老师|经理|哥|姐|叔|姨|医师|先生|女士)"
)


# ---------------------------------------------------------------------------
# 检测信号（§4.3：时长字数比异常 / VAD 切碎 / 语义残句）
# ---------------------------------------------------------------------------

def _duration_per_char_anomalous(duration_seconds, char_count):
    """中文语速约 0.2~0.6 s/字；落到区间外（或空文本却有音频）视为听错可疑。

    - char_count == 0 且 duration > 0 ：有声音但没识别出文本 → 异常。
    - ratio 偏小（过快）或偏大（过慢 / 停顿）→ 异常。
    """
    if char_count <= 0:
        return duration_seconds > 0
    ratio = duration_seconds / char_count
    return ratio < 0.15 or ratio > 1.2


def _has_anomaly_signals(text, context):
    """检测信号任一条成立即返回 True（听错风险高，需确认/重听）。

    context 约定键（调用方可选提供）：
    - ``duration_seconds`` : float  音频时长（秒）
    - ``char_count``       : int    文本字数
    - ``vad_fragments``    : int    VAD 切碎片段数（>=2 视为切碎）
    - ``is_semantic_fragment`` : bool  语义残句
    """
    duration = context.get("duration_seconds")
    char_count = context.get("char_count")
    if isinstance(duration, (int, float)) and isinstance(char_count, (int, float)):
        if _duration_per_char_anomalous(float(duration), int(char_count)):
            return True
    vad = context.get("vad_fragments")
    if isinstance(vad, int) and vad >= 2:
        return True
    if context.get("is_semantic_fragment"):
        return True
    return False


def _has_date_num_name(text):
    """数字 / 日期 / 时间 / 人名 高危词检测。"""
    if _DATE_PATTERN.search(text):
        return True
    if _NAME_PATTERN.search(text):
        return True
    if any(h in text for h in _HONORIFICS):
        return True
    if _NUM_PATTERN.search(text):
        return True
    return False


def _is_memory_write(text):
    return any(h in text for h in _MEMORY_WRITE_HINTS)


def _is_irreversible(text):
    return any(h in text for h in _IRREVERSIBLE_HINTS)


def _is_reversible_task(text):
    return any(h in text for h in _TASK_HINTS)


# ---------------------------------------------------------------------------
# 公共接口
# ---------------------------------------------------------------------------

def classify_risk(text: str, context: dict | None = None) -> str:
    """判定听错入口风险等级，返回 ``high`` / ``medium`` / ``low``。

    ``context`` 为可选检测信号字典（见 ``_has_anomaly_signals``）。
    判定顺序（从高到低）：
    1. 记忆写入 / 不可逆指令 / 听错信号异常 → ``high``；
    2. 数字·日期·人名高危（且非可逆任务语境）→ ``high``；
    3. 可逆任务 → ``medium``；
    4. 其余（闲聊）→ ``low``。
    """
    text = (text or "").strip()
    ctx = context or {}

    if _is_memory_write(text) or _is_irreversible(text) or _has_anomaly_signals(text, ctx):
        return RISK_HIGH

    # 任务语境中的数量（如"来两杯咖啡"）是可逆任务；独立数字/日期/人名陈述才升级高
    if _has_date_num_name(text) and not _is_reversible_task(text):
        return RISK_HIGH

    if _is_reversible_task(text):
        return RISK_MEDIUM

    return RISK_LOW


def needs_confirmation(text: str, context: dict | None = None) -> bool:
    """是否需要对 ``text`` 做复述确认（仅 ``high`` 风险需要）。"""
    return classify_risk(text, context) == RISK_HIGH


def apply_confirmation(user_confirmed: bool) -> bool:
    """``confirmed`` 只在用户复述确认后置 ``true``。

    本模块没有任何路径会自动把 ``confirmed`` 置 ``true``；唯一的置真入口
    是调用方在收到用户确认后传入 ``user_confirmed=True``。返回 ``bool`` 供
    MemEntry.confirmed 赋值。
    """
    return bool(user_confirmed)


def should_retain_audio(text: str, context: dict | None = None) -> bool:
    """音频零留存：听错入口处理**永不**保留原始音频，恒返回 ``False``。"""
    return False


def build_confirm_utterance(text: str) -> str:
    """构建复述确认话术，形如「四月十二号生日，对吧？」。

    剥离记忆写入引导前缀，保留核心陈述内容并追加确认问句。
    若原句已带确认问句/语气，则保持原样返回。
    """
    text = (text or "").strip()
    body = text
    for prefix in _CONFIRM_STRIP_PREFIXES:
        if body.startswith(prefix):
            body = body[len(prefix):].strip()
            break

    if not body:
        return "是这句，对吧？"

    if body.endswith(("吗", "吧", "呢", "？", "?")) or body.endswith("对吧") or body.endswith("对不对"):
        return body

    return f"{body}，对吧？"
