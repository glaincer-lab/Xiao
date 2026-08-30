"""M1-A 记忆基础层（backend/memv4.py）单元测试。

覆盖：MemEntry 五要素 schema 可实例化且字段名与 §3 一致、加密预留字段存在、
DataTrack 三层 append/落盘/重载、原始层零丢失断言、容量策略（上限 500 低置信淘汰）、
非法 kind fail-fast、payload 缺省为空 dict。仅标准库；本文件 MIT。

注：落盘临时目录用 `ROOT/.tmp` 下的 `makedirs` 子目录（而非 `tempfile`）。
原因：本仓库在 DeepSeek Harness 沙箱内运行时，`tempfile` 生成目录（系统 Temp +
`mkdtemp`）被沙箱拦截写入（Permission denied），而 `os.makedirs` 创建的
普通目录可正常读写；两种目录在真实终端均可用，故取此兼容写法。
"""
from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from backend.config import ROOT
from backend.memv4 import (
    DATA_TRACK_KINDS,
    PROFILE_MAX_ENTRIES,
    DataTrack,
    MemEntry,
    evict_low_confidence,
)


def _make_tmp_dir() -> Path:
    """在 workspace 的 `.tmp` 下创建可写临时目录（见模块 docstring 说明）。"""
    d = ROOT / ".tmp" / f"memv4_test_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


class MemEntrySchemaTest(unittest.TestCase):
    def test_mem_entry_instantiable_with_defaults(self) -> None:
        e = MemEntry(content="不喝咖啡")
        self.assertEqual(e.content, "不喝咖啡")
        self.assertEqual(e.scope, "global")
        self.assertEqual(e.source, "explicit")
        self.assertEqual(e.status, "active")
        self.assertFalse(e.confirmed)
        self.assertEqual(e.affective_luminance, 0)
        self.assertEqual(e.confidence, 1.0)
        self.assertTrue(e.id)

    def test_mem_entry_fields_match_spec_section3(self) -> None:
        # §3 五要素 schema 字段名必须与契约完全一致（预留给 M1-B~F 消费）
        e = MemEntry(
            id="mem_1",
            content="不喝咖啡",
            scope="global",
            scope_detail={"until": "2026-09-15"},
            effective_at="2026-08-30",
            source="explicit",
            status="active",
            confirmed=True,
            affective_luminance=3,
            confidence=0.9,
        )
        self.assertEqual(e.id, "mem_1")
        self.assertEqual(e.scope_detail, {"until": "2026-09-15"})
        self.assertEqual(e.effective_at, "2026-08-30")
        self.assertTrue(e.confirmed)
        self.assertEqual(e.affective_luminance, 3)
        self.assertAlmostEqual(e.confidence, 0.9)
        # 所有 §3 字段名齐全
        for name in (
            "id", "content", "scope", "scope_detail", "effective_at", "source",
            "status", "confirmed", "affective_luminance", "confidence",
        ):
            self.assertTrue(hasattr(e, name), f"缺少字段 {name}")

    def test_encryption_reserved_fields_present(self) -> None:
        # v4.1.1 加密预留字段存在（首版不启用，仅占位）
        e = MemEntry(content="敏感")
        self.assertFalse(e.encrypted)
        self.assertIsNone(e.enc_token)
        e2 = MemEntry(content="敏感", encrypted=True, enc_token="tok_abc")
        self.assertTrue(e2.encrypted)
        self.assertEqual(e2.enc_token, "tok_abc")

    def test_to_dict_roundtrip(self) -> None:
        e = MemEntry(content="只喝美式", confidence=0.8)
        d = e.to_dict()
        self.assertEqual(d["content"], "只喝美式")
        self.assertIn("encrypted", d)
        self.assertIn("enc_token", d)


class DataTrackTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_tmp_dir()
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        self.root = self._tmp
        self.track = DataTrack(root=self.root)

    def test_append_returns_id_and_count(self) -> None:
        i1 = self.track.append("session_logs", {"text": "记得买咖啡"})
        self.assertTrue(i1)
        self.assertEqual(self.track.count("session_logs"), 1)
        # 不同层独立计数
        self.track.append("raw_frames_meta", {"frame_id": "f1", "conclusion": "有人在笑"})
        self.assertEqual(self.track.count("raw_frames_meta"), 1)
        self.assertEqual(self.track.count("session_logs"), 1)

    def test_payload_defaults_to_empty_dict(self) -> None:
        i = self.track.append("session_logs")
        recs = self.track.items("session_logs")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["id"], i)
        self.assertIn("ts", recs[0])

    def test_reload_roundtrip(self) -> None:
        self.track.append("session_logs", {"text": "第一条"})
        self.track.append("session_logs", {"text": "第二条"})
        self.track.append("raw_frames_meta", {"frame_id": "x"})
        self.track.append("context_snapshots", {"snapshot": "s1"})
        # 用同一 root 重新构造（重载）
        track2 = DataTrack(root=self.root)
        self.assertEqual([r["text"] for r in track2.items("session_logs")], ["第一条", "第二条"])
        self.assertEqual(track2.count("raw_frames_meta"), 1)
        self.assertEqual(track2.count("context_snapshots"), 1)

    def test_zero_loss_session_logs_grows_monotonically(self) -> None:
        # 原始层零丢失：任一操作后 session_logs 行数只增不减
        track = DataTrack(root=self.root)
        base = track.count("session_logs")
        track.append("session_logs", {"text": "a"})
        track.append("raw_frames_meta", {"frame_id": "b"})
        track.append("session_logs", {"text": "c"})
        track.append("context_snapshots", {"snapshot": "d"})
        final = track.count("session_logs")
        self.assertGreaterEqual(final, base)
        self.assertEqual(final, base + 2)

    def test_mutation_of_items_does_not_affect_store(self) -> None:
        self.track.append("session_logs", {"text": "原样"})
        got = self.track.items("session_logs")
        got[0]["text"] = "被改了"
        self.assertEqual(self.track.items("session_logs")[0]["text"], "原样")

    def test_unknown_kind_fails_fast(self) -> None:
        with self.assertRaises(ValueError):
            self.track.append("unknown_kind", {"x": 1})
        with self.assertRaises(ValueError):
            self.track.count("unknown_kind")
        with self.assertRaises(ValueError):
            self.track.items("unknown_kind")

    def test_non_dict_payload_raises(self) -> None:
        with self.assertRaises(TypeError):
            self.track.append("session_logs", "not a dict")

    def test_kinds_are_exactly_three(self) -> None:
        self.assertEqual(set(DATA_TRACK_KINDS), {"session_logs", "raw_frames_meta", "context_snapshots"})

    def test_corrupt_file_resets(self) -> None:
        # 仿照 corrupt JSON 容错：损坏时重置为空，不让坏文件卡死后续写入
        p = self.root / "session_logs.json"
        p.write_text("{不是json", encoding="utf-8")
        track = DataTrack(root=self.root)
        self.assertEqual(track.count("session_logs"), 0)
        i = track.append("session_logs", {"text": "损坏后仍可写"})
        self.assertTrue(i)
        self.assertEqual(DataTrack(root=self.root).count("session_logs"), 1)


class ProfileCapacityTest(unittest.TestCase):
    def _mk(self, cid: str, conf: float, confirmed: bool = False) -> MemEntry:
        return MemEntry(id=cid, content=cid, confidence=conf, confirmed=confirmed)

    def test_within_limit_no_evict(self) -> None:
        entries = [self._mk(f"m{i}", 0.5) for i in range(3)]
        kept, removed = evict_low_confidence(entries, max_entries=500)
        self.assertEqual(len(kept), 3)
        self.assertEqual(removed, [])

    def test_over_limit_evicts_low_confidence(self) -> None:
        # 容量策略：上限 500，超限低置信淘汰
        entries = [self._mk(f"m{i}", conf) for i, conf in enumerate([0.1, 0.9, 0.3, 0.7])]
        kept, removed = evict_low_confidence(entries, max_entries=2)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(removed), 2)
        kept_ids = {e.id for e in kept}
        # 置信最高的两个留下
        self.assertEqual(kept_ids, {"m1", "m3"})

    def test_evict_prefers_unconfirmed_on_tie(self) -> None:
        # 同置信：先淘汰未确认者
        entries = [
            self._mk("a", 0.5, confirmed=True),
            self._mk("b", 0.5, confirmed=False),
        ]
        kept, removed = evict_low_confidence(entries, max_entries=1)
        self.assertEqual([e.id for e in removed], ["b"])
        self.assertEqual([e.id for e in kept], ["a"])

    def test_default_max_is_500(self) -> None:
        self.assertEqual(PROFILE_MAX_ENTRIES, 500)


if __name__ == "__main__":
    unittest.main()
