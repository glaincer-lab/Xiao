"""注意力传感器（M0 · M0-core §4.2）：四信号 + 进程黑名单硬阻断 + 事件发布。

仅标准库 + win32 API（GetLastInputInfo / GetForegroundWindow / 前台窗口样式），不引入第三方库。

订阅/发布契约（事件名已在 backend.event_bus.EVENT_TYPES 白名单，本模块只发布、不自增登记）：
    attention.fullscreen  {on: bool, 进程名: str}   发布者 M0 → 订阅者 M3（主动候选挂起）
    attention.sigh        {置信: float, 键鼠活跃: bool} 发布者 M0 → 订阅者 M2（仅星云形态）

信号与出口（M0-core §4.2 / §5 边界，写死）：
    - 键鼠空闲   GetLastInputInfo，60s 采样 → IDLE 判定/巩固触发/勿扰（**只读信号，无总线事件**）；
                  因 EVENT_REGISTRY 未登记 attention.idle，故不发布事件，仅暴露 is_idle()/idle_seconds()。
    - 全屏检测   前台窗口样式查询 → 发布 attention.fullscreen（on/off + 进程名）。
    - 前台进程名 GetForegroundWindow 调用时查询 → 供进程黑名单（查询即用，零留存）。
    - 系统负载   CPU/内存采样 → 档位自适应（**只读信号，无总线事件**；EVENT_REGISTRY 无 attention.load）。
    - 叹气启发式 KWS 音频能量/音高（三阶段：硬门→键鼠二元无感校准→30s 平滑上线）；
                  仅星云静默守护形态，禁止触发主动决策或打断——只发 attention.sigh 信号，动作交给 M2。

隐私红线（M0-core §5）：数据只做二元/计数判断，**禁止存储原始输入内容**（音频原文/键值序列零留存）。
安全提权项（M0-core §4.2）：游戏/网银/支付类进程前台时，VLM 截屏与鼠标模拟在工具分发层直接拒绝授权。
门控（M0-core §3 授权中心）：screen_awareness 是注意力传感器这类隐私敏感能力的总闸门——
    授权关闭时传感器不采集、不发布（黑名单守卫为 fail-closed 安全项，见下方说明）。

仅供标准库；MIT。
"""

from __future__ import annotations

import os
import time
from typing import Any

from backend.authorization import AuthorizationCenter
from backend.event_bus import bus as _default_bus

# ---------------------------------------------------------------------------
# 进程黑名单（M0-core §4.2 写死）：游戏 / 网银 / 支付 类。
# 前台命中 → 工具分发层拒 VLM 截屏与鼠标模拟，拒绝话术固定。
# ---------------------------------------------------------------------------
_BLOCK_MSG = "这个窗口我不看也不动，放心。"

# 游戏类：常见 PC 客户端进程名（反外挂误判封号风险，按需扩展）。
_BLOCKLIST_GAMES: frozenset[str] = frozenset({
    "cs2.exe", "counter-strike 2.exe",
    "leagueclient.exe", "league of legends.exe",
    "dota2.exe",
    "genshinimpact.exe", "yuanshen.exe",
    "starrail.exe", "hkrpg.exe",
    "valorant.exe", "overwatch.exe",
    "pubg.exe", "tslgame.exe",
})

# 网银/支付类：这类客户端进程名随版本/银行各异，故用保守子串命中（含下述关键词即阻断）。
_BLOCKLIST_KEYWORDS: tuple[str, ...] = ("netbank", "网银", "ebank", "bankclient", "网银客户端")

# 对外暴露：黑名单内容 + 判断函数（供测试与工具层引用）。
BLACKLIST_GAMES = _BLOCKLIST_GAMES
BLACKLIST_KEYWORDS = _BLOCKLIST_KEYWORDS
BLOCK_MESSAGE = _BLOCK_MSG


def is_blacklisted(process_name: str | None) -> bool:
    """前台进程名是否命中黑名单（游戏精确匹配 + 网银/支付关键词）。"""
    if not process_name:
        return False
    name = str(process_name).strip().lower()
    if name in _BLOCKLIST_GAMES:
        return True
    return any(k in name for k in _BLOCKLIST_KEYWORDS)


# ---------------------------------------------------------------------------
# Win32 探测层（可注入）：真实现用 ctypes 调 user32/kernel32；测试用 Fake 替换。
# 统一在此收口，避免传感器模块到处 import ctypes.windll。
# ---------------------------------------------------------------------------
class Win32Provider:
    """Windows 底层探测（GetLastInputInfo / GetForegroundWindow / 窗口样式 / 系统负载）。

    全部调用懒加载、try/except 包裹；非 win32 平台不 import ctypes.windll，避免 ImportError。
    """

    def __init__(self) -> None:
        self._user32 = None
        self._kernel32 = None

    def _u(self):  # 0.001s 内懒加载 user32
        if self._user32 is None:
            import ctypes
            self._user32 = ctypes.windll.user32
        return self._user32

    def _k(self):
        if self._kernel32 is None:
            import ctypes
            self._kernel32 = ctypes.windll.kernel32
        return self._kernel32

    # ---- 键鼠空闲 ----
    def last_input_seconds(self) -> float:
        """返回自上次输入以来的空闲秒数（GetLastInputInfo + GetTickCount64，处理 32 位回绕）。"""
        import ctypes
        user32, kernel32 = self._u(), self._k()

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        user32.GetLastInputInfo.restype = ctypes.c_int
        user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0
        tick = (kernel32.GetTickCount64() & 0xFFFFFFFF)
        idle_ms = (tick - lii.dwTime) & 0xFFFFFFFF
        return idle_ms / 1000.0

    # ---- 前台窗口 ----
    def foreground_hwnd(self) -> int:
        return int(self._u().GetForegroundWindow() or 0)

    def process_name_of(self, hwnd: int) -> str | None:
        """前台进程 exe 基名（小写）；打开失败返回 None（不阻断，视为不可知）。"""
        if not hwnd:
            return None
        import ctypes
        user32, kernel32 = self._u(), self._k()
        user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return None
        try:
            size = ctypes.c_ulong(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return None
            return os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(h)

    @staticmethod
    def _rect_type():
        import ctypes
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        return RECT

    def window_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        import ctypes
        user32 = self._u()
        RECT = self._rect_type()
        user32.GetWindowRect.restype = ctypes.c_int
        user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
        r = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return None
        return (r.left, r.top, r.right, r.bottom)

    def monitor_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        import ctypes
        user32 = self._u()
        RECT = self._rect_type()

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        user32.GetMonitorInfoW.restype = ctypes.c_int
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
        hmon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not hmon:
            return None
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return None
        r = mi.rcMonitor
        return (r.left, r.top, r.right, r.bottom)

    def window_style(self, hwnd: int) -> int:
        import ctypes
        user32 = self._u()
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        return int(user32.GetWindowLongW(hwnd, -16))  # GWL_STYLE

    # ---- 系统负载 ----
    @staticmethod
    def _filetime64(f) -> int:
        return (f.dwHighDateTime << 32) | f.dwLowDateTime

    def _read_cpu_times(self):
        import ctypes
        kernel32 = self._k()

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]

        kernel32.GetSystemTimes.restype = ctypes.c_int
        kernel32.GetSystemTimes.argtypes = [
            ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
        ]
        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return (self._filetime64(idle), self._filetime64(kernel), self._filetime64(user))

    def cpu_percent(self) -> float | None:
        """CPU 利用率（GetSystemTimes 两次采样差值估算；失败返回 None）。"""
        t1 = self._read_cpu_times()
        if t1 is None:
            return None
        time.sleep(0.1)
        t2 = self._read_cpu_times()
        if t2 is None:
            return None
        idle = t2[0] - t1[0]
        kernel = t2[1] - t1[1]
        user = t2[2] - t1[2]
        total = kernel + user
        if total <= 0:
            return 0.0
        busy = total - idle  # kernel 含 idle
        return max(0.0, min(100.0, busy / total * 100.0))

    def memory_percent(self) -> float | None:
        import ctypes
        kernel32 = self._k()

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        kernel32.GlobalMemoryStatusEx.restype = ctypes.c_int
        kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
        mem = MEMORYSTATUSEX()
        mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
            return None
        return float(mem.dwMemoryLoad)


# ---------------------------------------------------------------------------
# 叹气启发式：KWS 音频能量/音高 → 只留存聚合统计，零留存原始采样。
# 三阶段：①硬门（影子期风格，能量包络须显著）→ ②键鼠二元无感校准 → ③30s 指数平滑（≤5% 变化率）。
# ---------------------------------------------------------------------------
class SighCollector:
    """KWS 音频帧聚合器：只保留均值/峰值/计数，**零留存原始能量/音高序列**。"""

    def __init__(self) -> None:
        self._frames = 0
        self._sum_energy = 0.0
        self._peak_energy = 0.0
        self._sum_pitch = 0.0
        self._peak_pitch = 0.0

    def add(self, energy: float | None, pitch: float | None = None) -> None:
        """喂入单个音频帧的能量/音高；只累加统计，丢弃原始值。"""
        if energy is None:
            return
        e = float(energy)
        self._frames += 1
        self._sum_energy += e
        self._peak_energy = max(self._peak_energy, e)
        if pitch is not None:
            p = float(pitch)
            self._sum_pitch += p
            self._peak_pitch = max(self._peak_pitch, p)

    def summary(self) -> dict[str, float]:
        n = self._frames or 1
        return {
            "frames": float(self._frames),
            "mean_energy": self._sum_energy / n,
            "peak_energy": self._peak_energy,
            "peak_pitch": self._peak_pitch,
        }

    def retained_raw(self) -> None:
        """确认零留存：始终返回 None（不保留任何原始采样）。"""
        return None


def classify_sigh(profile: dict[str, float], input_active: bool, prev_confidence: float = 0.0) -> float:
    """三阶段叹气启发式 → 置信度 [0,1]。

    profile: SighCollector.summary() 的聚合统计（frames/mean_energy/peak_energy/peak_pitch）。
    input_active: 键鼠是否活跃（二元判断；无感校准：活跃时若怀疑是工作叹气，适当降权）。
    prev_confidence: 上一帧置信度，用于 30s 指数平滑，单次变化率 ≤5%（平滑上线）。

    纯函数、无副作用；返回值仅供 publish，不触发任何动作/打断。
    """
    peak = float(profile.get("peak_energy", 0.0))
    mean = float(profile.get("mean_energy", 0.0))
    frames = float(profile.get("frames", 0.0))

    # ① 硬门：样本太少 or 包络不明显 → 不判为叹气（影子期风格：宁可漏报不误报）。
    if frames < 5 or peak <= 0.0 or mean <= 0.0:
        return 0.0
    if peak / mean < 1.6:  # 能量峰值需显著高于均值（典型叹气为「快速抬升后衰减」包络）
        return 0.0

    # 基础置信：峰值相对均值的显著性（0.5~1.0 区间）。
    base = min(1.0, (peak / mean) / 3.0)
    base = 0.5 + 0.5 * base

    # ② 键鼠二元无感校准：用户正大幅输入时，叹气更可能是「工作叹气」，降权（不误报为情绪信号）。
    if input_active:
        base *= 0.6

    # ③ 30s 平滑上线：相邻两次置信变化率 ≤5%（指数平滑）。
    smooth = prev_confidence + 0.05 * (base - prev_confidence)
    return round(max(0.0, min(1.0, smooth)), 3)


# ---------------------------------------------------------------------------
# 注意力传感器（对外主 API）
# ---------------------------------------------------------------------------
class AttentionSensor:
    def __init__(self, auth: Any | None = None, bus: Any | None = None, win32: Any | None = None) -> None:
        self._auth = auth if auth is not None else AuthorizationCenter()
        self._bus = bus if bus is not None else _default_bus
        self._win32 = win32 if win32 is not None else Win32Provider()

    @property
    def enabled(self) -> bool:
        """screen_awareness 是注意力传感器这类隐私敏感能力的总闸门（默认关）。"""
        try:
            return bool(self._auth.is_granted("screen_awareness"))
        except Exception:  # noqa: BLE001  授权不可用/异常一律视为关闭（fail-closed）
            return False

    # ---- 只读探测器（检测本身不依赖授权；发布/采集由调用方门控） ----
    def idle_seconds(self) -> float:
        """距上次键鼠输入的空闲秒数。"""
        try:
            return float(self._win32.last_input_seconds() or 0.0)
        except Exception:  # noqa: BLE001
            return 0.0

    def is_idle(self, threshold: float = 15 * 60) -> bool:
        """空闲判定信号（M0-core §4.4：锁屏/无键鼠 >15min 触发巩固）。只读信号，不发布事件。"""
        return self.idle_seconds() >= threshold

    def foreground_process_name(self) -> str | None:
        """GetForegroundWindow 调用时查询前台进程名（查询即用，零留存）。供进程黑名单。"""
        try:
            hwnd = self._win32.foreground_hwnd()
        except Exception:  # noqa: BLE001
            return None
        if not hwnd:
            return None
        try:
            return self._win32.process_name_of(hwnd)
        except Exception:  # noqa: BLE001
            return None

    def is_fullscreen(self, hwnd: int | None = None) -> bool:
        """前台窗口是否全屏：窗口矩形覆盖所在监视器 ±2px 且无标题栏（WS_CAPTION 未置位）。"""
        if hwnd is None:
            try:
                hwnd = self._win32.foreground_hwnd()
            except Exception:  # noqa: BLE001
                hwnd = 0
        if not hwnd:
            return False
        try:
            rect = self._win32.window_rect(hwnd)
            mon = self._win32.monitor_rect(hwnd)
            style = self._win32.window_style(hwnd)
        except Exception:  # noqa: BLE001
            return False
        if rect is None or mon is None:
            return False
        covers = (
            rect[0] <= mon[0] + 2 and rect[1] <= mon[1] + 2
            and rect[2] >= mon[2] - 2 and rect[3] >= mon[3] - 2
        )
        if not covers:
            return False
        WS_CAPTION = 0x00C00000  # WS_BORDER | WS_DLGFRAME
        return (style & WS_CAPTION) == 0

    def system_load(self) -> dict[str, float]:
        """CPU/内存采样 → 档位自适应信号（只读，不发布事件）。"""
        cpu = None
        mem = None
        try:
            cpu = self._win32.cpu_percent()
        except Exception:  # noqa: BLE001
            pass
        try:
            mem = self._win32.memory_percent()
        except Exception:  # noqa: BLE001
            pass
        cpu = float(cpu or 0.0)
        mem = float(mem or 0.0)
        return {"cpu_percent": cpu, "mem_percent": mem}

    def load_tier(self) -> str:
        """按负载记忆档位：load < 60% → slim；<85% → standard；否则 power（资源档位自适应，只读）。"""
        load = (self.system_load().get("cpu_percent", 0.0)
                + self.system_load().get("mem_percent", 0.0)) / 2.0
        if load < 60:
            return "slim"
        if load < 85:
            return "standard"
        return "power"

    # ---- 采集 + 发布（门控：授权关闭时一律不采集、不发布） ----
    def sample_window(self) -> dict[str, Any] | None:
        """采集当前窗口快照（门控：enabled 关闭 → 不采集，返回 None）。"""
        if not self.enabled:
            return None
        try:
            hwnd = self._win32.foreground_hwnd()
        except Exception:  # noqa: BLE001
            return None
        if not hwnd:
            return None  # 无前台窗口 → 无窗口态可采集
        # 仅保留二元/状态信息；不落盘、不缓存原始输入。
        return {
            "fullscreen": self.is_fullscreen(hwnd),
            "process_name": self._win32.process_name_of(hwnd),
            "idle_seconds": self.idle_seconds(),
        }

    def emit_fullscreen(self) -> dict[str, Any] | None:
        """发布 attention.fullscreen {on, 进程名}（门控：enabled 关闭 → 不发布，返回 None）。"""
        snap = self.sample_window()
        if snap is None:
            return None
        payload = {"on": bool(snap["fullscreen"]), "进程名": snap["process_name"]}
        self._bus.emit("attention.fullscreen", payload)
        return payload

    def emit_sigh(self, confidence: float, input_active: bool) -> dict[str, Any] | None:
        """发布 attention.sigh {置信, 键鼠活跃}（门控：enabled 关闭 → 不发布，返回 None）。

        仅星云静默守护形态：只发信号、不做主动决策、不打断（动作交由 M2 消费端决定）。
        """
        if not self.enabled:
            return None
        conf = max(0.0, min(1.0, float(confidence)))
        payload = {"置信": conf, "键鼠活跃": bool(input_active)}
        self._bus.emit("attention.sigh", payload)
        return payload

    def tick(self) -> dict[str, Any] | None:
        """60s 采样节拍：采集四信号并发布全屏状态（门控：关闭则只返回 None、不发布）。"""
        if not self.enabled:
            return None
        return self.emit_fullscreen()


# ---------------------------------------------------------------------------
# 工具分发层守卫（进程黑名单硬阻断）——供 tools/computer.py 与 tools/system_control.py 挂载。
# ---------------------------------------------------------------------------
_default_sensor: AttentionSensor | None = None


def _sensor() -> AttentionSensor:
    global _default_sensor
    if _default_sensor is None:
        _default_sensor = AttentionSensor()
    return _default_sensor


def guard_blacklisted_window() -> str | None:
    """工具分发层守卫：前台窗口进程名命中黑名单 → 返回拒绝话术（fail-closed）。

    - 安全提权项：**不存储、不发布**过程名，仅在工具分发（VLM 截屏/鼠标模拟）入口拒绝授权，
      符合 M0-core §5 零留存边界。
    - 查询失败（非 win32 / 打开进程失败）→ 视为不可知，返回 None（不误拦）。
    - 返回非 None 时话术固定为 <block_message>。
    """
    name = _sensor().foreground_process_name()
    if is_blacklisted(name):
        return _BLOCK_MSG
    return None


__all__ = [
    "AttentionSensor",
    "Win32Provider",
    "SighCollector",
    "classify_sigh",
    "is_blacklisted",
    "guard_blacklisted_window",
    "BLOCK_MESSAGE",
    "BLACKLIST_GAMES",
    "BLACKLIST_KEYWORDS",
    "_sensor",
]
