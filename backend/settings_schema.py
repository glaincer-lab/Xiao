"""设置字段注册表：前端据此自动渲染，后端据此保存。

每个字段的 path 必须与 config.yaml 的真实键一致（点路径）。
- type: checkbox | select | slider | number | text | textarea | multiselect | guide
- reload: soft（保存后即时生效）| restart（需重启后端）
- list: true 表示该 textarea 按「每行一个」拆成列表保存
- option.status: ok（已实现）| planned（预留接口，后端尚未接入）
- show_if: {"path": "...", "value": "..."} 仅当该 path 当前值等于 value 时才显示
- guide: 说明块文本，用于引导接入（如 vLLM 安装、Key 获取）

设计定位：本文件是「接入接口」——使用者自行装好模型/获取 Key 后，
在这里填地址/路径/模型名接入；不负责准备或下载任何本地模型。
"""

from __future__ import annotations

from backend.config import OLLAMA_BASE_URL, OLLAMA_MODEL, OMNI_BASE_URL, OMNI_MODEL

# 标签页顺序：大模型是独立板块，放在播报（TTS）之后
GROUPS = [
    {"key": "wake", "label": "唤醒"},
    {"key": "asr", "label": "识别"},
    {"key": "tts", "label": "播报"},
    {"key": "llm", "label": "大模型"},
    {"key": "exec", "label": "执行"},
    {"key": "perms", "label": "权限"},
    {"key": "memory", "label": "存储"},
]

# edge-tts 官方中文音色（Azure 标准 voice 名）
TTS_VOICES = [
    {"value": "zh-CN-XiaoxiaoNeural", "label": "晓晓（女）"},
    {"value": "zh-CN-XiaoyiNeural", "label": "晓伊（女）"},
    {"value": "zh-CN-YunxiNeural", "label": "云希（男）"},
    {"value": "zh-CN-YunjianNeural", "label": "云健（男）"},
    {"value": "zh-CN-YunyangNeural", "label": "云扬（男）"},
    {"value": "zh-CN-YunxiaNeural", "label": "云夏（男）"},
    {"value": "zh-CN-liaoning-XiaobeiNeural", "label": "晓北（东北女声）"},
    {"value": "zh-CN-shaanxi-XiaoniNeural", "label": "晓妮（陕西女声）"},
]

TTS_RATES = [
    {"value": "-50%", "label": "很慢 -50%"},
    {"value": "-30%", "label": "较慢 -30%"},
    {"value": "-10%", "label": "稍慢 -10%"},
    {"value": "+0%", "label": "正常"},
    {"value": "+10%", "label": "稍快 +10%"},
    {"value": "+30%", "label": "较快 +30%"},
    {"value": "+50%", "label": "很快 +50%"},
]

PERM_CATEGORIES = [
    {"value": "network", "label": "网络访问"},
    {"value": "write_outside", "label": "写工作区外"},
    {"value": "delete", "label": "删除文件"},
    {"value": "install", "label": "安装软件包"},
    {"value": "system", "label": "修改系统"},
]

# 一体化 MiniCPM-o（vLLM）接入引导：作为「大脑」通过 OpenAI 兼容端点接入
VLLM_GUIDE = (
    "作为「大脑」接入：MiniCPM-o 通过 vLLM 的 OpenAI 兼容端点接入，填地址 + 模型即可（本地无 Key）。\n"
    "部署：NVIDIA GPU（MiniCPM-o 8B 全模态，约 5~6GB 显存）+ vLLM（MiniCPM-o 多模态需 vllm-omni 分支，见官方 README）。\n"
    f"步骤：① 装 vLLM → ② 下载 {OMNI_MODEL} → ③ 启动服务 → ④ 上方填地址与模型名。\n"
    "注意：其「一体化语音」（唤醒 / 识别 / 播报）仍待后续接入。"
)

# 其它环节选「一体化」时的引导：统一指向大模型板块填写 vLLM 接入
OMNI_GUIDE = (
    "一体化 MiniCPM-o 会接管本环节（唤醒 / 识别 / 语音输出一并完成）。\n"
    "请在【大模型】板块点「＋ 添加模型」，供应商选「一体化 MiniCPM-o（vLLM）」，"
    "填好服务地址与模型名后设为主用。"
)

SCHEMA: list[dict] = [
    # ---- 唤醒 ----
    {"path": "wake_word.engine", "label": "唤醒方式", "type": "select", "group": "wake", "reload": "restart",
     "options": [
         {"value": "sherpa", "label": "本地关键词 sherpa-ONNX（普通话）", "status": "ok"},
         {"value": "omni", "label": "一体化 MiniCPM-o（方言、端到端）", "status": "ok"},
     ]},
    {"path": "wake_word.enabled", "label": "唤醒开关", "type": "checkbox", "group": "wake", "reload": "restart",
     "show_if": {"path": "wake_word.engine", "value": "sherpa"}},
    {"path": "wake_word.keyword", "label": "唤醒词", "type": "text", "group": "wake", "reload": "restart",
     "show_if": {"path": "wake_word.engine", "value": "sherpa"},
     "hint": "改唤醒词需同步改拼音，且需重启后端"},
    {"path": "wake_word.pinyin", "label": "拼音", "type": "text", "group": "wake", "reload": "restart",
     "show_if": {"path": "wake_word.engine", "value": "sherpa"},
     "hint": "声母韵母空格分隔，如：x iǎo èr"},
    {"path": "wake_word.threshold", "label": "灵敏度", "type": "slider", "group": "wake", "reload": "restart",
     "show_if": {"path": "wake_word.engine", "value": "sherpa"},
     "min": 0.1, "max": 1.0, "step": 0.05, "hint": "越小越灵敏，也越容易误唤醒"},
    {"path": "wake_word.model_dir", "label": "本地模型目录", "type": "text", "group": "wake", "reload": "restart",
     "show_if": {"path": "wake_word.engine", "value": "sherpa"},
     "hint": "sherpa-onnx KWS 模型目录，含 tokens.txt 与 encoder/decoder/joiner 三个 onnx"},
    {"path": "_guide.wake.omni", "label": "引导", "type": "guide", "group": "wake",
     "show_if": {"path": "wake_word.engine", "value": "omni"}, "guide": OMNI_GUIDE},
    {"path": "bargein.enabled", "label": "播报中可打断", "type": "checkbox", "group": "wake", "reload": "soft"},

    # ---- 识别 ----
    {"path": "asr.provider", "label": "识别方式", "type": "select", "group": "asr", "reload": "restart",
     "options": [
         {"value": "cloud", "label": "云端 Fun-ASR / Qwen", "status": "ok"},
         {"value": "local", "label": "本地 FunASR", "status": "ok"},
         {"value": "omni", "label": "一体化 MiniCPM-o（本地）", "status": "ok"},
     ]},
    {"path": "asr.cloud.provider", "label": "云端服务商", "type": "select", "group": "asr", "reload": "restart",
     "show_if": {"path": "asr.provider", "value": "cloud"},
     "options": [{"value": "aliyun", "label": "阿里云", "status": "ok"}]},
    {"path": "asr.cloud.model", "label": "云端模型", "type": "select", "group": "asr", "reload": "restart",
     "show_if": {"path": "asr.provider", "value": "cloud"},
     "options": [
         {"value": "qwen-audio-3.0-asr-flash-streaming", "label": "Qwen-Audio（普通话，16k，默认）", "status": "ok"},
         {"value": "fun-asr-flash-8k-realtime", "label": "Fun-ASR（方言备选：重庆话/四川话/粤语，8k）", "status": "ok"},
     ],
     "hint": "默认 qwen-audio（16kHz 普通话）；说方言时可切 fun-asr（8kHz）"},
    {"path": "asr.cloud.api_key", "label": "API Key", "type": "text", "group": "asr", "reload": "restart",
     "show_if": {"path": "asr.provider", "value": "cloud"},
     "hint": "阿里云百炼（Model Studio）的 API Key；留空则读环境变量 DASHSCOPE_API_KEY"},
    {"path": "asr.local.engine", "label": "本地引擎", "type": "select", "group": "asr", "reload": "restart",
     "show_if": {"path": "asr.provider", "value": "local"},
     "options": [{"value": "funasr", "label": "FunASR", "status": "ok"}]},
    {"path": "asr.local.model", "label": "本地模型", "type": "text", "group": "asr", "reload": "restart",
     "show_if": {"path": "asr.provider", "value": "local"},
     "hint": "如 paraformer-zh（留空可用默认）"},
    {"path": "asr.local.model_dir", "label": "本地模型目录", "type": "text", "group": "asr", "reload": "restart",
     "show_if": {"path": "asr.provider", "value": "local"},
     "hint": "自行下载的 FunASR 模型目录；留空则按模型名自动获取"},
    {"path": "_guide.asr.omni", "label": "引导", "type": "guide", "group": "asr",
     "show_if": {"path": "asr.provider", "value": "omni"}, "guide": OMNI_GUIDE},
    {"path": "vad.silero_threshold", "label": "静音灵敏度", "type": "slider", "group": "asr", "reload": "restart",
     "min": 0, "max": 1.0, "step": 0.05, "hint": "越小声也判为语音，越灵敏"},
    {"path": "vad.min_speech_ms", "label": "最短语音(毫秒)", "type": "number", "group": "asr", "reload": "restart"},
    {"path": "vad.silence_ms", "label": "静音判定(毫秒)", "type": "number", "group": "asr", "reload": "restart",
     "hint": "停多久算一句话说完"},
    {"path": "vad.session_timeout_ms", "label": "会话超时(毫秒)", "type": "number", "group": "asr", "reload": "restart",
     "hint": "说完后多久没下文就休眠"},

    # ---- 播报 ----
    {"path": "tts.provider", "label": "播报方式", "type": "select", "group": "tts", "reload": "restart",
     "options": [
         {"value": "qwen_rt", "label": "Qwen 实时流式（快，语音跟字幕）", "status": "ok"},
         {"value": "edge", "label": "edge-tts 免费云", "status": "ok"},
         {"value": "piper", "label": "本地 Piper（离线保底）", "status": "ok"},
         {"value": "omni", "label": "MiniCPM-o（本地 vLLM）", "status": "ok"},
     ]},
    {"path": "tts.voice", "label": "音色", "type": "select", "group": "tts", "reload": "restart",
     "show_if": {"path": "tts.provider", "value": "edge"},
     "options": TTS_VOICES},
    {"path": "tts.rate", "label": "语速", "type": "select", "group": "tts", "reload": "restart",
     "show_if": {"path": "tts.provider", "value": "edge"},
     "options": TTS_RATES},
    {"path": "_guide.tts.piper", "label": "引导", "type": "guide", "group": "tts",
     "guide": "本地 Piper：完全离线合成，作为断网保底。\n依赖：pip install piper-tts；声库 models/zh_CN-chaowen-medium.onnx（已随项目提供）。"},
    {"path": "_guide.tts.omni", "label": "引导", "type": "guide", "group": "tts",
     "guide": f"MiniCPM-o 通过本地 vLLM-omni 服务播报（llm.omni 的 base_url，默认 {OMNI_BASE_URL}）。\n需本机已启动 vLLM-omni，否则无声音。"},

    # ---- 大模型（独立板块，接入接口） ----
    {"path": "llm.provider", "label": "模型来源", "type": "select", "group": "llm", "reload": "restart",
     "options": [
         {"value": "cloud", "label": "云端（供应商 API）", "status": "ok"},
         {"value": "local", "label": "本地 Ollama（OpenAI 兼容）", "status": "ok"},
         {"value": "omni", "label": "一体化 MiniCPM-o（本地 vLLM）", "status": "ok"},
     ]},
    {"path": "llm.timeout_sec", "label": "响应超时(秒)", "type": "number", "group": "llm", "reload": "restart",
     "min": 5, "max": 300, "hint": "调用大模型超过该秒数即判超时；云端慢网络可适当调大"},
    {"path": "llm.cloud.provider", "label": "云端服务商", "type": "select", "group": "llm", "reload": "restart",
     "show_if": {"path": "llm.provider", "value": "cloud"},
     "options": [
         {"value": "deepseek", "label": "DeepSeek", "status": "ok"},
         {"value": "dashscope", "label": "通义千问", "status": "ok"},
         {"value": "openai", "label": "OpenAI", "status": "ok"},
         {"value": "glm", "label": "智谱 GLM", "status": "ok"},
         {"value": "kimi", "label": "Kimi", "status": "ok"},
     ]},
    {"path": "llm.cloud.model", "label": "云端模型", "type": "text", "group": "llm", "reload": "restart",
     "show_if": {"path": "llm.provider", "value": "cloud"}},
    {"path": "llm.cloud.base_url", "label": "接口地址", "type": "text", "group": "llm", "reload": "restart",
     "show_if": {"path": "llm.provider", "value": "cloud"},
     "hint": "留空则用服务商默认地址"},
    {"path": "llm.cloud.temperature", "label": "随机度", "type": "slider", "group": "llm", "reload": "restart",
     "show_if": {"path": "llm.provider", "value": "cloud"},
     "min": 0, "max": 1.0, "step": 0.05, "hint": "越低越稳定，越高越发散"},
    {"path": "llm.cloud.context_input", "label": "上下文窗口(输入)", "type": "select", "group": "llm", "reload": "restart",
     "show_if": {"path": "llm.provider", "value": "cloud"},
     "options": [
         {"value": "", "label": "跟随模型默认", "status": "ok"},
         {"value": "131072", "label": "128k", "status": "ok"},
         {"value": "262144", "label": "256k", "status": "ok"},
         {"value": "524288", "label": "512k", "status": "ok"},
         {"value": "1048576", "label": "1M", "status": "ok"},
     ],
     "hint": "保存备用（历史裁剪与多模态预算，后续版本生效）"},
    {"path": "llm.cloud.context_output", "label": "上下文窗口(输出)", "type": "select", "group": "llm", "reload": "restart",
     "show_if": {"path": "llm.provider", "value": "cloud"},
     "options": [
         {"value": "", "label": "跟随模型默认", "status": "ok"},
         {"value": "4096", "label": "4k", "status": "ok"},
         {"value": "16384", "label": "16k", "status": "ok"},
         {"value": "32768", "label": "32k", "status": "ok"},
         {"value": "131072", "label": "128k", "status": "ok"},
     ],
     "hint": "回复长度上限；回复被截断时可调大"},
    {"path": "llm.cloud.tool_rounds", "label": "工具调用轮数", "type": "number", "group": "llm", "reload": "soft",
     "show_if": {"path": "llm.provider", "value": "cloud"},
     "min": 1, "max": 2000, "hint": "单次任务最多工具调用轮数，默认 500"},
    {"path": "llm.cloud.thinking", "label": "思考模式", "type": "select", "group": "llm", "reload": "restart",
     "show_if": {"path": "llm.provider", "value": "cloud"},
     "options": [
         {"value": "default", "label": "跟随模型默认", "status": "ok"},
         {"value": "on", "label": "开启思考", "status": "ok"},
         {"value": "off", "label": "关闭思考", "status": "ok"},
     ],
     "hint": "保存备用（流式接入后生效）"},
    {"path": "llm.cloud.image_input", "label": "支持图片输入", "type": "checkbox", "group": "llm", "reload": "restart",
     "show_if": {"path": "llm.provider", "value": "cloud"},
     "hint": "开启后输入框出现贴图/截图按钮；需模型本身支持视觉（如 qwen-vl-max、glm-4v），DeepSeek Flash Vision 支持多模态"},
    {"path": "llm.cloud.top_p", "label": "采样 Top P", "type": "text", "group": "llm", "reload": "restart",
     "show_if": {"path": "llm.provider", "value": "cloud"},
     "hint": "0~1，留空跟随模型默认"},
    {"path": "llm.cloud.top_k", "label": "采样 Top K", "type": "text", "group": "llm", "reload": "restart",
     "show_if": {"path": "llm.provider", "value": "cloud"},
     "hint": "留空不发送；DeepSeek/OpenAI/Kimi 暂不支持透传（仅保存）"},
    {"path": "llm.local.base_url", "label": "本地地址", "type": "text", "group": "llm", "reload": "restart",
     "show_if": {"path": "llm.provider", "value": "local"},
     "hint": f"Ollama 地址，默认 {OLLAMA_BASE_URL}"},
    {"path": "llm.local.model", "label": "本地模型", "type": "text", "group": "llm", "reload": "restart",
     "show_if": {"path": "llm.provider", "value": "local"},
     "hint": f"如 {OLLAMA_MODEL}"},
    {"path": "llm.local.temperature", "label": "随机度", "type": "slider", "group": "llm", "reload": "restart",
     "show_if": {"path": "llm.provider", "value": "local"},
     "min": 0, "max": 1.0, "step": 0.05},
    {"path": "llm.omni.base_url", "label": "vLLM 服务地址", "type": "text", "group": "llm", "reload": "restart",
     "show_if": {"path": "llm.provider", "value": "omni"},
     "hint": f"如 {OMNI_BASE_URL}"},
    {"path": "llm.omni.model", "label": "一体化模型名", "type": "text", "group": "llm", "reload": "restart",
     "show_if": {"path": "llm.provider", "value": "omni"},
     "hint": f"如 {OMNI_MODEL}"},
    {"path": "_guide.llm.omni", "label": "引导", "type": "guide", "group": "llm",
     "show_if": {"path": "llm.provider", "value": "omni"}, "guide": VLLM_GUIDE},
    {"path": "agent.system_prompt", "label": "系统提示词（人设）", "type": "textarea", "group": "llm", "reload": "soft",
     "hint": "留空则用内置默认"},
    {"path": "agent.max_history", "label": "记忆轮数", "type": "number", "group": "llm", "reload": "soft",
     "min": 1, "max": 50, "hint": "记住最近几轮对话"},

    # ---- 执行 ----
    {"path": "router.mode", "label": "路由模式", "type": "select", "group": "exec", "reload": "soft",
     "options": [{"value": "auto", "label": "自动"}, {"value": "chat", "label": "聊天"}, {"value": "dsh", "label": "DSH"}]},
    {"path": "router.dsh_keywords", "label": "DSH 关键词（每行一个）", "type": "textarea", "group": "exec", "reload": "soft", "list": True},
    {"path": "bridge.dsh_command", "label": "DSH 命令", "type": "text", "group": "exec", "reload": "restart"},
    {"path": "bridge.timeout_sec", "label": "超时(秒)", "type": "number", "group": "exec", "reload": "restart"},
    {"path": "agent.workspace", "label": "工作目录", "type": "text", "group": "exec", "reload": "restart"},
    {"path": "tasks.max_concurrent", "label": "任务并发数", "type": "number", "group": "exec", "reload": "restart", "min": 1, "max": 1,
     "hint": "固定 1：DSH 桥为单进程槽，并发任务会互相取消"},
    {"path": "tools.computer.enabled", "label": "语音操电脑", "type": "checkbox", "group": "exec", "reload": "soft",
     "hint": "开启后可语音控制鼠标键盘/窗口/截屏看图；点按、打字、热键、关窗逐次语音审批"},
    {"path": "tools.computer.confirm_ttl_seconds", "label": "审批放行有效期(秒)", "type": "number", "group": "exec", "reload": "soft",
     "min": 60, "hint": "同类语音操电脑动作批准后，多久内免重复询问（默认 1800 秒=30 分钟）"},
    # ---- 权限 ----
    {"path": "approval.enabled", "label": "审批开关", "type": "checkbox", "group": "perms", "reload": "soft"},
    {"path": "approval.timeout_ms", "label": "审批超时(毫秒)", "type": "number", "group": "perms", "reload": "soft"},
    {"path": "approval.allow_phrases", "label": "允许词（每行一个）", "type": "textarea", "group": "perms", "reload": "soft", "list": True},
    {"path": "approval.deny_phrases", "label": "拒绝词（每行一个）", "type": "textarea", "group": "perms", "reload": "soft", "list": True},
    {"path": "perms.standing_grants", "label": "常驻授权", "type": "multiselect", "group": "perms", "reload": "restart",
     "options": PERM_CATEGORIES,
     "hint": "⚠ 按类别放行、粒度较粗：勾选「删除/安装/修改系统」后，DSH 的所有命令类操作（bash/pwsh）将自动放行（含 rm 级删除），请仅对可信任务开启"},

    # ---- 存储 ----
    {"path": "memory.storage_budget_mb", "label": "存储预算", "type": "select", "group": "memory", "reload": "soft",
     "options": [
          {"value": "100", "label": "轻量 100MB"},
          {"value": "300", "label": "标准 300MB（默认）"},
          {"value": "500", "label": "充裕 500MB"},
          {"value": "2048", "label": "超大 2GB"},
          {"value": "custom", "label": "自定义"},
     ]},
    {"path": "memory.storage_budget_custom_mb", "label": "自定义预算(MB)", "type": "number", "group": "memory", "reload": "soft",
     "show_if": {"path": "memory.storage_budget_mb", "value": "custom"}},
    {"path": "memory.short_window_days", "label": "短期全量窗口(天)", "type": "number", "group": "memory", "reload": "soft",
     "hint": "原文保留天数，默认730(2年)"},
    {"path": "memory.long_window_days", "label": "长期向量窗口(天)", "type": "number", "group": "memory", "reload": "soft",
     "hint": "向量保留天数，默认3650(10年)"},
    {"path": "memory.retrieval_top_k", "label": "向量召回 Top-K", "type": "number", "group": "memory", "reload": "soft",
     "min": 1, "max": 64, "hint": "每次注入召回的记忆条数，默认8"},
]
