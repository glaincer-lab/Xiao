"""M6-M4 纪念锚点见证（backend/m6/memorial.py）测试。

验收断言（M6-growth.md §7 / §4.4，与 M1.6 联动）：
1. 见证询问月度 + 可关闭：关闭后不再问；30 天内只问一次。
2. 复用 M1.5 人物卡存储（set_memorial_anchor / set_memorial_ask），不重复造存储——
   memorial.json 只落盘「上次询问日期」（persona 无此字段），锚点/开关状态一律读 persona。

临时目录用 ROOT/.tmp 下 makedirs 子目录（沙箱拒写系统 Temp，见 test_memv4.py）。
"""
from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from backend.config import ROOT
from backend.memv1.persona import PersonaStore
from backend.m6.memorial import DEFAULT_PROMPT, MemorialWitness


def _make_tmp_dir() -> Path:
    """在 workspace 的 .tmp 下创建可写临时目录（沙箱拒写系统 Temp）。"""
    d = ROOT / ".tmp" / f"m6_memorial_{uuid.uuid4().hex[:8]}"
    os.makedirs(d, exist_ok=True)
    return d


class MemorialWitnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_tmp_dir()
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        self.persona = PersonaStore(root=self._tmp)
        self.clock: dict[str, date] = {"now": date(2026, 3, 1)}
        self.witness = MemorialWitness(
            self.persona,
            now_fn=lambda: self.clock["now"],
            root=self._tmp,
        )

    # ---- 工具 ----

    def _add_anchor(self, name: str = "外婆", relation: str = "外婆") -> None:
        """建人物卡并打纪念锚点（丧失关系 → 只读记忆，persona 内部置 memorial_ask=True）。"""
        self.persona.add_kinship_card(name, relation)
        self.persona.set_memorial_anchor(name, True)

    def _advance(self, days: int) -> None:
        self.clock["now"] = self.clock["now"] + timedelta(days=days)

    def _last_ask_file(self) -> Path:
        return self._tmp / "memorial.json"

    # ---- 验收 1：月度 + 可关闭 ----

    def test_anchor_card_gets_prompt(self) -> None:
        """纪念锚点卡首次见证询问返回询问语。"""
        self._add_anchor()
        self.assertEqual(self.witness.witness_prompt("外婆"), DEFAULT_PROMPT)
        self.assertIn("想聊聊", DEFAULT_PROMPT)

    def test_monthly_once_within_interval(self) -> None:
        """30 天内只问一次；距上次询问满 30 天再问。"""
        self._add_anchor()
        self.assertIsNotNone(self.witness.witness_prompt("外婆"))  # t0 问
        self._advance(29)
        self.assertIsNone(self.witness.witness_prompt("外婆"))      # t0+29 < 30 不问
        self._advance(1)                                            # t0+30
        self.assertEqual(self.witness.witness_prompt("外婆"), DEFAULT_PROMPT)
        self._advance(29)                                           # t0+59
        self.assertIsNone(self.witness.witness_prompt("外婆"))      # 距上次 29 < 30 不问
        self._advance(1)                                            # t0+60
        self.assertEqual(self.witness.witness_prompt("外婆"), DEFAULT_PROMPT)  # 距上次满 30 再问

    def test_disabled_never_asks(self) -> None:
        """关闭见证询问后不再问（即使距上次询问已满 30 天）。"""
        self._add_anchor()
        self.witness.disable("外婆")
        self.assertIsNone(self.witness.witness_prompt("外婆"))
        self._advance(40)
        self.assertIsNone(self.witness.witness_prompt("外婆"))
        # 开关确实写进了 persona 存储（复用，非本层副本）
        self.assertFalse(self.persona.get_kinship_card("外婆").memorial_ask)

    def test_disable_after_first_ask(self) -> None:
        """问过一次后关闭，之后一直不再问。"""
        self._add_anchor()
        self.assertIsNotNone(self.witness.witness_prompt("外婆"))
        self.witness.disable("外婆")
        self._advance(60)
        self.assertIsNone(self.witness.witness_prompt("外婆"))

    def test_enable_after_disable_resumes_monthly(self) -> None:
        """重新开启后恢复月度节奏（受 last_ask 约束，不满 30 天仍不问）。"""
        self._add_anchor()
        self.witness.disable("外婆")
        self.witness.enable("外婆")
        # 开启后首问照常（从未问过，无冷却）；之后受 30 天节奏约束
        self.assertEqual(self.witness.witness_prompt("外婆"), DEFAULT_PROMPT)
        self._advance(29)
        self.assertIsNone(self.witness.witness_prompt("外婆"))
        self._advance(1)
        self.assertEqual(self.witness.witness_prompt("外婆"), DEFAULT_PROMPT)

    def test_interval_days_customizable(self) -> None:
        """可配置询问间隔（如 7 天节奏，供 M3 调度微调）。"""
        self.persona.add_kinship_card("爷爷", "爷爷")
        self.persona.set_memorial_anchor("爷爷", True)
        w = MemorialWitness(
            self.persona,
            now_fn=lambda: self.clock["now"],
            interval_days=7,
            root=self._tmp,
        )
        self.assertIsNotNone(w.witness_prompt("爷爷"))
        self._advance(6)
        self.assertIsNone(w.witness_prompt("爷爷"))
        self._advance(1)
        self.assertEqual(w.witness_prompt("爷爷"), DEFAULT_PROMPT)

    # ---- 验收 2：复用 persona 存储，不重复造 ----

    def test_anchor_state_lives_in_persona(self) -> None:
        """锚点/开关状态由 persona 管理；memorial.json 只含 last_ask，无开关副本。"""
        self._add_anchor()
        card = self.persona.get_kinship_card("外婆")
        self.assertTrue(card.memorial_anchor)
        self.assertTrue(card.readonly)  # 丧失关系 → 只读记忆（persona 内部联动）
        self.assertTrue(card.memorial_ask)
        # 未询问前不落盘
        self.assertFalse(self._last_ask_file().exists())
        self.witness.witness_prompt("外婆")
        data = json.loads(self._last_ask_file().read_text(encoding="utf-8"))
        self.assertEqual(set(data.keys()), {"last_ask"})
        self.assertIn("外婆", data["last_ask"])
        # 没有任何 anchor/ask 副本字段
        self.assertNotIn("memorial_anchor", data)
        self.assertNotIn("memorial_ask", data)

    def test_last_ask_survives_reload(self) -> None:
        """重建 witness（同 root）后仍记住上次询问时间，30 天内不重复问。"""
        self._add_anchor()
        self.assertIsNotNone(self.witness.witness_prompt("外婆"))
        self._advance(10)
        reloaded = MemorialWitness(
            self.persona,
            now_fn=lambda: self.clock["now"],
            root=self._tmp,
        )
        self.assertIsNone(reloaded.witness_prompt("外婆"))
        self._advance(20)  # 距首次 30 天整
        self.assertEqual(reloaded.witness_prompt("外婆"), DEFAULT_PROMPT)

    def test_non_anchor_card_never_asks(self) -> None:
        """未打纪念锚点的普通人物卡不触发见证询问。"""
        self.persona.add_kinship_card("老同学", "老同学")
        self.assertIsNone(self.witness.witness_prompt("老同学"))

    def test_unknown_card_returns_none(self) -> None:
        """人物卡不存在时静默返回 None（不打扰、不抛裸错）。"""
        self.assertIsNone(self.witness.witness_prompt("不存在的名字"))

    def test_grief_phase_delegation(self) -> None:
        """复用 M1.6 grief_phase（3/7/30 天节奏供 M3 哀伤调度消费）。"""
        self.persona.add_grief_tag("外婆")
        self.assertEqual(self.witness.grief_phase("外婆"), "d3")  # 当天打标 → 3 天内
        self.assertEqual(self.witness.grief_phase("不存在"), "")

    def test_now_fn_accepts_datetime(self) -> None:
        """now_fn 返回 datetime 时同样归一化到日期。"""
        self.persona.add_kinship_card("父亲", "父亲")
        self.persona.set_memorial_anchor("父亲", True)
        dt_clock = {"now": datetime(2026, 5, 1, 18, 30)}
        w = MemorialWitness(
            self.persona,
            now_fn=lambda: dt_clock["now"],
            root=self._tmp,
        )
        self.assertIsNotNone(w.witness_prompt("父亲"))
        dt_clock["now"] = datetime(2026, 5, 30, 9, 0)   # 距首问 29 天
        self.assertIsNone(w.witness_prompt("父亲"))
        dt_clock["now"] = datetime(2026, 5, 31, 0, 0)   # 距首问 30 天整
        self.assertEqual(w.witness_prompt("父亲"), DEFAULT_PROMPT)

    def test_corrupt_file_resets(self) -> None:
        """memorial.json 损坏时重置为空，不卡死启动。"""
        self._add_anchor()
        self.witness.witness_prompt("外婆")
        self._last_ask_file().write_text("{ not valid json", encoding="utf-8")
        w = MemorialWitness(
            self.persona,
            now_fn=lambda: self.clock["now"],
            root=self._tmp,
        )
        self.assertEqual(w.witness_prompt("外婆"), DEFAULT_PROMPT)  # 视为从未问过


if __name__ == "__main__":
    unittest.main()
