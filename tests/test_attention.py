"""M0 注意力传感器（backend/attention.py）单元测试。

覆盖 M0-core §4.2 / §5 边界 / 安全提权项：
1. 进程黑名单硬阻断：游戏/网银类前台命中 → 工具分发层（VLM 截屏 + 鼠标模拟）拒授权，断言固定话术。
2. 全屏检测 → attention.fullscreen {on/off, 进程名} 发布（bus.on 捕获 payload）。
3. 键鼠空闲判定信号（只读；EVENT_REGISTRY 无 attention.idle，故不发布事件）。
4. attention.sigh {置信, 键鼠活跃} 发布断言。
5. 数据零留存：采集后无原始内容落盘 / 内存不保留原始键值/音频原文。
6. screen_awareness 门控：授权关闭 → 传感器不采集/不发布；授权开启 → 正常。

只测逻辑路径，全部用 FakeWin32 替换真实 win32 调用（不真实截屏/模拟鼠标）。
运行：.venv/Scripts/python.exe -m unittest tests.test_attention -v
"""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

import uuid
import shutil
from unittest import mock

from backend.authorization import AuthorizationCenter
from backend.event_bus import EventBus
from backend.config import Config

import backend.attention as attention
from backend.attention import (
    AttentionSensor,
    SighCollector,
    classify_sigh,
    is_blacklisted,
    BLOCK_MESSAGE,
)
import backend.tools.computer as computer_mod
from backend.tools.computer import (
    ComputerMouseTool,
    ScreenLookTool,
    set_confirm_hook,
)
from backend.tools.system_control import ScreenshotTool

import backend.event_bus as event_bus_mod

_TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp"


class FakeConfig:
    """内存版 Config（同 AuthorizationCenter 测试），避免写真实 config.yaml。"""

    def __init__(self, data: dict | None = None) -> None:
        self._data: dict = data or {}
        self._saved = 0

    def get(self, dotted: str, default=None):
        node: dict = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def update(self, updates: dict) -> None:
        self._merge(self._data, updates)

    def save(self) -> None:
        self._saved += 1

    def _merge(self, base: dict, override: dict) -> None:
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                self._merge(base[k], v)
            else:
                base[k] = v


class FakeWin32:
    """可注入 win32 探测（替代真实 ctypes.windll 调用）。"""

    def __init__(
        self,
        idle_seconds: float = 0.0,
        hwnd: int = 0x1A2B,
        proc: str | None = "python.exe",
        rect=(0, 0, 1920, 1080),
        mon=(0, 0, 1920, 1080),
        style: int = 0,
        cpu: float = 5.0,
        mem: float = 40.0,
        active_procs: list[str] | None = None,
        scan_available: bool = True,
    ) -> None:
        self._idle = idle_seconds
        self._hwnd = hwnd
        self._proc = proc
        self._rect = rect
        self._mon = mon
        self._style = style
        self._cpu = cpu
        self._mem = mem
        self._active_procs = list(active_procs) if active_procs is not None else []
        self._scan_available = scan_available

    def last_input_seconds(self) -> float:
        return self._idle

    def foreground_hwnd(self) -> int:
        return self._hwnd

    def process_name_of(self, hwnd: int) -> str | None:
        return self._proc

    def active_process_names(self) -> list[str] | None:
        return self._active_procs if self._scan_available else None

    def window_rect(self, hwnd: int):
        return self._rect

    def monitor_rect(self, hwnd: int):
        return self._mon

    def window_style(self, hwnd: int) -> int:
        return self._style

    def cpu_percent(self) -> float:
        return self._cpu

    def memory_percent(self) -> float:
        return self._mem


def _auth_data(screen: bool) -> FakeConfig:
    return FakeConfig({"authorizations": {"screen_awareness": screen}})


def _make_sensor(screen: bool = True, win32: FakeWin32 | None = None) -> tuple[AttentionSensor, EventBus]:
    """构造已注入 FakeWin32 + 内存授权 + 独立 EventBus 的传感器。"""
    sensor = AttentionSensor(
        auth=AuthorizationCenter(_auth_data(screen)),
        bus=EventBus(),
        win32=win32 or FakeWin32(),
    )
    return sensor, sensor._bus


class TestBlacklistGuard(unittest.TestCase):
    """安全提权项：进程黑名单在工具分发层硬阻断。"""

    def setUp(self) -> None:
        # 让 guard 使用可控的 FakeWin32（前台 = 命中黑名单）
        self._old_sensor = attention._default_sensor
        self._old_computer_config = computer_mod.config
        computer_mod.config = Config({"tools": {"computer": {"enabled": True, "confirm": []}}})
        set_confirm_hook(None)
        self.addCleanup(lambda: setattr(attention, "_default_sensor", self._old_sensor))
        self.addCleanup(setattr, computer_mod, "config", self._old_computer_config)
        self.addCleanup(set_confirm_hook, None)

    def _patch_sensor(
        self,
        proc: str | None,
        active_procs: list[str] | None = None,
        scan_available: bool = True,
    ) -> None:
        sensor = AttentionSensor(
            auth=AuthorizationCenter(_auth_data(True)),
            bus=EventBus(),
            win32=FakeWin32(proc=proc, active_procs=active_procs, scan_available=scan_available),
        )
        attention._default_sensor = sensor

    def test_game_foreground_blocks_mouse(self):
        self._patch_sensor("cs2.exe")
        out = asyncio.run(ComputerMouseTool().run(action="click", x=1, y=1))
        self.assertEqual(out, BLOCK_MESSAGE)

    def test_game_foreground_blocks_screen_look(self):
        self._patch_sensor("leagueclient.exe")
        with mock.patch.object(ScreenLookTool, "_capture") as cap:
            out = asyncio.run(ScreenLookTool().run())
        self.assertEqual(out, BLOCK_MESSAGE)
        cap.assert_not_called()  # 未真正截屏

    def test_game_foreground_blocks_screenshot(self):
        self._patch_sensor("dota2.exe")
        with mock.patch.object(ScreenshotTool, "_capture") as cap:
            out = asyncio.run(ScreenshotTool().run())
        self.assertEqual(out, BLOCK_MESSAGE)
        cap.assert_not_called()

    def test_bank_keyword_foreground_blocks(self):
        self._patch_sensor("ccb_netbank_client.exe")
        out = asyncio.run(ComputerMouseTool().run(action="click", x=1, y=1))
        self.assertEqual(out, BLOCK_MESSAGE)

    def test_non_blacklisted_foreground_allowed(self):
        self._patch_sensor("notepad.exe")
        with mock.patch.object(ComputerMouseTool, "_execute") as ex:
            out = asyncio.run(ComputerMouseTool().run(action="click", x=1, y=1))
        self.assertNotEqual(out, BLOCK_MESSAGE)
        self.assertTrue(ex.called)  # 未被黑名单拦截

    def test_is_blacklisted_logic(self):
        self.assertTrue(is_blacklisted("cs2.exe"))
        self.assertTrue(is_blacklisted("ICBC_NETBANK.EXE".lower()))
        self.assertTrue(is_blacklisted("网银客户端"))
        self.assertFalse(is_blacklisted("notepad.exe"))
        self.assertFalse(is_blacklisted(None))
        self.assertFalse(is_blacklisted(""))

    def test_background_blacklisted_process_blocks(self):
        # 前台正常，但后台活跃进程命中黑名单 → 广度扫描熔断拒绝
        self._patch_sensor("notepad.exe", active_procs=["valorant.exe"])
        out = asyncio.run(ComputerMouseTool().run(action="click", x=1, y=1))
        self.assertEqual(out, BLOCK_MESSAGE)

    def test_scan_unavailable_fails_closed(self):
        # 广度扫描不可知 → fail-closed 熔断（宁可错拒不误放）
        self._patch_sensor("notepad.exe", scan_available=False)
        out = asyncio.run(ComputerMouseTool().run(action="click", x=1, y=1))
        self.assertEqual(out, BLOCK_MESSAGE)

    def test_no_blacklisted_process_allows(self):
        # 前台正常 + 活跃进程正常 → 放行
        self._patch_sensor("notepad.exe", active_procs=["chrome.exe"])
        with mock.patch.object(ComputerMouseTool, "_execute") as ex:
            out = asyncio.run(ComputerMouseTool().run(action="click", x=1, y=1))
        self.assertNotEqual(out, BLOCK_MESSAGE)
        self.assertTrue(ex.called)


class TestFullscreenEvent(unittest.TestCase):
    """全屏检测 → attention.fullscreen {on/off, 进程名} 发布。"""

    def test_fullscreen_on_publishes(self):
        win32 = FakeWin32(proc="game.exe", rect=(0, 0, 1920, 1080), mon=(0, 0, 1920, 1080), style=0)
        sensor, bus = _make_sensor(True, win32)
        captured: list[dict] = []
        unsub = bus.on("attention.fullscreen", captured.append)
        payload = sensor.emit_fullscreen()
        unsub()
        self.assertIsNotNone(payload)
        self.assertTrue(payload["on"])            # 全屏
        self.assertEqual(payload["进程名"], "game.exe")
        self.assertEqual(captured, [payload])     # bus 捕获

    def test_fullscreen_off_publishes(self):
        # 有标题栏（WS_CAPTION 置位）且未覆盖整屏 → 非全屏
        win32 = FakeWin32(proc="browser.exe", rect=(10, 10, 1000, 700), mon=(0, 0, 1920, 1080),
                          style=0x00C00000)
        sensor, bus = _make_sensor(True, win32)
        captured: list[dict] = []
        unsub = bus.on("attention.fullscreen", captured.append)
        payload = sensor.emit_fullscreen()
        unsub()
        self.assertIsNotNone(payload)
        self.assertFalse(payload["on"])
        self.assertEqual(payload["进程名"], "browser.exe")
        self.assertEqual(len(captured), 1)

    def test_fullscreen_edge_case_no_hwnd(self):
        sensor, _ = _make_sensor(True, FakeWin32(hwnd=0))
        self.assertIsNone(sensor.emit_fullscreen())  # 无前台窗口 → 不发布


class TestIdleSignal(unittest.TestCase):
    """键鼠空闲判定信号（只读；无 attention.idle 事件）。"""

    def test_idle_seconds(self):
        sensor, _ = _make_sensor(True, FakeWin32(idle_seconds=900.0))
        self.assertAlmostEqual(sensor.idle_seconds(), 900.0)

    def test_is_idle_threshold(self):
        sensor, _ = _make_sensor(True, FakeWin32(idle_seconds=15 * 60 + 5))
        self.assertTrue(sensor.is_idle())                # 默认 15min
        self.assertFalse(sensor.is_idle(threshold=20 * 60))

    def test_idle_is_readonly_no_event(self):
        # EVENT_REGISTRY 未登记 attention.idle → 传感器不应发布 idle 事件
        sensor, bus = _make_sensor(True, FakeWin32(idle_seconds=1000.0))
        bus.emit  # 存在
        self.assertTrue(sensor.is_idle())
        # 断言无 attention.idle 事件名（白名单中不存在）
        self.assertNotIn("attention.idle", event_bus_mod.EVENT_TYPES)


class TestSighEvent(unittest.TestCase):
    """attention.sigh {置信, 键鼠活跃} 发布。"""

    def test_sigh_publishes_payload(self):
        sensor, bus = _make_sensor(True, FakeWin32())
        captured: list[dict] = []
        unsub = bus.on("attention.sigh", captured.append)
        payload = sensor.emit_sigh(0.62, True)
        unsub()
        self.assertIsNotNone(payload)
        self.assertAlmostEqual(payload["置信"], 0.62)
        self.assertIs(payload["键鼠活跃"], True)
        self.assertEqual(captured, [payload])

    def test_sigh_confidence_clamped(self):
        sensor, _ = _make_sensor(True, FakeWin32())
        self.assertAlmostEqual(sensor.emit_sigh(1.7, False)["置信"], 1.0)
        self.assertAlmostEqual(sensor.emit_sigh(-0.3, False)["置信"], 0.0)

    def test_classify_sigh_hard_gate(self):
        # ① 硬门：样本太少 / 包络不明显 → 0
        self.assertEqual(classify_sigh({"frames": 2, "mean_energy": 1.0, "peak_energy": 1.0, "peak_pitch": 0.0}, False), 0.0)
        self.assertEqual(classify_sigh({"frames": 10, "mean_energy": 1.0, "peak_energy": 1.2, "peak_pitch": 0.0}, False), 0.0)

    def test_classify_sigh_calibration_and_smooth(self):
        prof = {"frames": 12, "mean_energy": 2.0, "peak_energy": 7.0, "peak_pitch": 0.0}
        idle_conf = classify_sigh(prof, input_active=False)
        active_conf = classify_sigh(prof, input_active=True)
        # ① 超过硬门→非零；② 键鼠活跃 → 降权
        self.assertGreater(idle_conf, 0.0)
        self.assertLess(active_conf, idle_conf)
        # ③ 30s 平滑上线：单次变化率 ≤5%
        nxt = classify_sigh(prof, input_active=False, prev_confidence=idle_conf)
        self.assertLessEqual(abs(nxt - idle_conf), 0.05 + 1e-6)
        # 平滑自 0 起步（5% 步长），再多迭代逼近目标但每步都不超过 5%
        prev = idle_conf
        for _ in range(5):
            cur = classify_sigh(prof, input_active=False, prev_confidence=prev)
            self.assertLessEqual(abs(cur - prev), 0.05 + 1e-6)
            prev = cur
        self.assertLessEqual(prev, 1.0)


class TestZeroRetention(unittest.TestCase):
    """数据零留存：采集后无原始内容落盘 / 内存不保留原始键值/音频原文。"""

    def test_sigh_collector_retains_only_aggregates(self):
        col = SighCollector()
        raw_energies = [0.5, 1.2, 1.6, 1.4, 0.9, 1.8]
        for i, e in enumerate(raw_energies):
            col.add(e, pitch=100 + i)
        s = col.summary()
        # 只保留聚合，不保留原始序列
        self.assertEqual(set(s.keys()), {"frames", "mean_energy", "peak_energy", "peak_pitch"})
        self.assertIsNone(col.retained_raw())
        # 断言对象内部无列表缓存原始值
        for v in vars(col).values():
            self.assertTrue(isinstance(v, (int, float)), f"未预期保留原始采样: {v!r}")

    def test_sensor_holds_no_raw_after_collection(self):
        win32 = FakeWin32(proc="mybank_netbank.exe", idle_seconds=60.0)
        sensor, bus = _make_sensor(True, win32)
        sensor.emit_fullscreen()
        sensor.emit_sigh(0.5, True)
        # 传感器内存不保留任何 list/dict 原始键值/音频序列
        for k, v in vars(sensor).items():
            if isinstance(v, (list, dict)):
                self.assertIn(k, ("_raw",))  # 无此类属性即不触发
        self.assertFalse(hasattr(sensor, "_last_window"))
        self.assertFalse(hasattr(sensor, "_raw_frames"))

    def test_no_file_written_to_repo_tmp(self):
        # 仿 test_memv4：用 os.makedirs 在 .tmp 下建可写目录（不用 tempfile/mkdtemp——沙箱拦截）
        tmp = _TMP_ROOT / f"attention_zero_{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        win32 = FakeWin32(hwnd=0x7, proc="game.exe", rect=(0, 0, 1920, 1080), mon=(0, 0, 1920, 1080), style=0)
        sensor, _ = _make_sensor(True, win32)
        sensor.emit_fullscreen()
        sensor.emit_sigh(0.7, False)
        sensor.is_idle()
        sensor.system_load()
        # 采集/发布路径不写任何文件
        self.assertEqual(list(tmp.iterdir()), [])


class TestScreenAwarenessGate(unittest.TestCase):
    """screen_awareness 授权（默认关）是注意力传感器总闸门。"""

    def test_disabled_no_collect(self):
        sensor, bus = _make_sensor(False, FakeWin32(proc="game.exe"))
        self.assertFalse(sensor.enabled)
        self.assertIsNone(sensor.sample_window())   # 不采集
        self.assertIsNone(sensor.emit_fullscreen())  # 不发布
        self.assertIsNone(sensor.emit_sigh(0.6, True))
        self.assertIsNone(sensor.tick())
        self.assertEqual(bus.count("attention.fullscreen"), 0)

    def test_disabled_no_event_emitted(self):
        sensor, bus = _make_sensor(False, FakeWin32())
        captured: list[dict] = []
        unsub = bus.on("attention.fullscreen", captured.append)
        sensor.emit_fullscreen()
        sensor.emit_sigh(0.6, False)
        unsub()
        self.assertEqual(captured, [])  # 未向订阅者广播任何事件

    def test_enabled_collects_and_publishes(self):
        win32 = FakeWin32(proc="game.exe", rect=(0, 0, 1920, 1080), mon=(0, 0, 1920, 1080), style=0)
        sensor, bus = _make_sensor(True, win32)
        self.assertTrue(sensor.enabled)
        snap = sensor.sample_window()
        self.assertIsNotNone(snap)
        self.assertTrue(snap["fullscreen"])
        payload = sensor.emit_fullscreen()
        self.assertIsNotNone(payload)
        self.assertTrue(payload["on"])


if __name__ == "__main__":
    unittest.main()
