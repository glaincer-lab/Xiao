"""backend/audit/（T8 · 可审计回放）单元测试。

覆盖：run 事件被追加式记录、可按 run_id 回放（replay）、scorecard 质量打点、
run 级工作区隔离（每个 run 独立记录目录）、命名无 homerail_、xiao_ 前缀。
运行：python -m unittest tests.test_xiao_audit -v
"""
from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from backend.audit import (
    FACT_EVENT_TYPES,
    XiaoAuditor,
    XiaoFactPlane,
    XiaoReplay,
    XiaoScorecard,
    build_auditor,
)

ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / "backend" / "audit"
_TEST_LOG_DIR = ROOT / "logs" / "_test_audit"


def _new_base() -> str:
    """在可写工作区下建临时 audit 基目录（避开受限系统临时区，防 PermissionError）。"""
    d = _TEST_LOG_DIR / uuid.uuid4().hex[:8]
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _rm_tree(p: str) -> None:
    shutil.rmtree(p, ignore_errors=True)


def _ok_result(name: str, content: str = "ok") -> dict:
    return {"message": {"callId": "c1", "name": name, "content": content, "isError": False}}


def _err_result(name: str, content: str = "boom") -> dict:
    return {"message": {"callId": "c1", "name": name, "content": content, "isError": True}}


class FactPlaneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = _new_base()
        self.plane = XiaoFactPlane(base_dir=self.base)

    def tearDown(self) -> None:
        _rm_tree(self.base)

    def test_append_and_replay_order(self) -> None:
        self.plane.append("r1", "tool/call", {"name": "clock"}, ts=1.0)
        self.plane.append("r1", "assistant/chunk", {"text": "hi"}, ts=2.0)
        self.plane.append("r1", "turn/end", {"reason": "completed"}, ts=3.0)
        facts = self.plane.facts("r1")
        self.assertEqual([f.event for f in facts], ["tool/call", "assistant/chunk", "turn/end"])
        self.assertEqual([f.seq for f in facts], [1, 2, 3])

    def test_append_only_never_rewrites(self) -> None:
        self.plane.append("r1", "tool/call", {"name": "clock"}, ts=1.0)
        path = self.plane.event_path("r1")
        first = path.read_text(encoding="utf-8")
        self.plane.append("r1", "turn/end", {"reason": "completed"}, ts=2.0)
        second = path.read_text(encoding="utf-8")
        self.assertIn("tool/call", first)
        self.assertTrue(second.startswith(first))
        self.assertGreater(second.count("\n"), first.count("\n"))

    def test_run_workspace_isolation(self) -> None:
        self.plane.append("rA", "tool/call", {"name": "clock"})
        self.plane.append("rB", "tool/call", {"name": "weather"})
        self.assertNotEqual(self.plane.run_dir("rA"), self.plane.run_dir("rB"))
        self.assertEqual([f.event for f in self.plane.facts("rA")], ["tool/call"])
        self.assertEqual([f.event for f in self.plane.facts("rB")], ["tool/call"])
        self.assertEqual([f.payload["name"] for f in self.plane.facts("rA")], ["clock"])
        self.assertEqual([f.payload["name"] for f in self.plane.facts("rB")], ["weather"])
        self.assertEqual(set(self.plane.runs()), {"rA", "rB"})

    def test_missing_run_returns_empty(self) -> None:
        self.assertEqual(self.plane.facts("nonexistent"), [])

    def test_seq_resumes_across_instances(self) -> None:
        self.plane.append("r1", "tool/call", {"name": "clock"})
        self.plane.append("r1", "tool/result", _ok_result("clock"))
        plane2 = XiaoFactPlane(base_dir=self.base)  # 新实例（模拟重启）
        plane2.append("r1", "turn/end", {"reason": "completed"})
        facts = plane2.facts("r1")
        self.assertEqual([f.seq for f in facts], [1, 2, 3])


class ReplayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = _new_base()
        self.plane = XiaoFactPlane(base_dir=self.base)

    def tearDown(self) -> None:
        _rm_tree(self.base)

    def test_replay_merges_chunks_into_message(self) -> None:
        self.plane.append("r1", "tool/call", {"name": "web_search", "arguments": {"q": "x"}}, ts=1.0)
        self.plane.append("r1", "assistant/chunk", {"text": "Let me "}, ts=2.0)
        self.plane.append("r1", "assistant/chunk", {"text": "search."}, ts=3.0)
        self.plane.append("r1", "tool/result", _ok_result("web_search"), ts=4.0)
        self.plane.append("r1", "assistant/message", {"text": "Done"}, ts=5.0)
        tl = XiaoReplay(self.plane).replay("r1")
        events = [e["event"] for e in tl]
        self.assertEqual(events, ["tool/call", "assistant/message", "tool/result", "assistant/message"])
        self.assertEqual(tl[1]["text"], "Let me search.")
        self.assertEqual(tl[3]["text"], "Done")

    def test_replay_render_is_text(self) -> None:
        self.plane.append("r1", "tool/call", {"name": "clock"}, ts=1.0)
        self.plane.append("r1", "turn/end", {"reason": "completed"}, ts=2.0)
        text = XiaoReplay(self.plane).render("r1")
        self.assertIn("tool/call clock", text)
        self.assertIn("turn/end completed", text)


class ScorecardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = _new_base()
        self.plane = XiaoFactPlane(base_dir=self.base)

    def tearDown(self) -> None:
        _rm_tree(self.base)

    def test_scorecard_ok_run(self) -> None:
        self.plane.append("r1", "tool/call", {"name": "clock"})
        self.plane.append("r1", "tool/result", _ok_result("clock"))
        self.plane.append("r1", "assistant/chunk", {"text": "现在是 10 点"})
        self.plane.append("r1", "turn/end", {"reason": "completed"})
        card = XiaoScorecard(self.plane).score("r1")
        self.assertEqual(card["tools_called"], 1)
        self.assertEqual(card["tools_ok"], 1)
        self.assertEqual(card["tools_error"], 0)
        self.assertEqual(card["tool_error_rate"], 0.0)
        self.assertGreater(card["assistant_chars"], 0)
        self.assertEqual(card["quality_score"], 100.0)

    def test_scorecard_error_run_lowers_quality(self) -> None:
        for i in range(3):
            self.plane.append("r1", "tool/call", {"name": "clock"})
        self.plane.append("r1", "tool/result", _ok_result("clock"))
        self.plane.append("r1", "tool/result", _ok_result("clock"))
        self.plane.append("r1", "tool/result", _err_result("clock"))
        self.plane.append("r1", "assistant/chunk", {"text": "done"})
        self.plane.append("r1", "turn/end", {"reason": "completed"})
        card = XiaoScorecard(self.plane).score("r1")
        self.assertEqual(card["tools_error"], 1)
        self.assertEqual(card["tools_ok"], 2)
        self.assertAlmostEqual(card["tool_error_rate"], 1 / 3, places=3)
        self.assertLess(card["quality_score"], 100.0)
        self.assertGreater(card["quality_score"], 0.0)

    def test_scorecard_empty_run(self) -> None:
        card = XiaoScorecard(self.plane).score("r1")
        self.assertEqual(card["tools_called"], 0)
        self.assertEqual(card["tools_error"], 0)
        self.assertLess(card["quality_score"], 100.0)

    def test_scorecard_tools_by_name(self) -> None:
        self.plane.append("r1", "tool/call", {"name": "clock"})
        self.plane.append("r1", "tool/call", {"name": "weather"})
        self.plane.append("r1", "tool/result", _ok_result("clock"))
        self.plane.append("r1", "tool/result", _ok_result("weather"))
        card = XiaoScorecard(self.plane).score("r1")
        self.assertEqual(card["tools_by_name"], {"clock": 1, "weather": 1})


class AuditorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = _new_base()
        self.auditor = build_auditor(base_dir=self.base)

    def tearDown(self) -> None:
        _rm_tree(self.base)

    def test_records_five_event_types(self) -> None:
        self.auditor.handle_event("tool/call", {"run_id": "r1", "name": "clock", "callId": "c1"})
        self.auditor.handle_event("tool/result", {"run_id": "r1", "message": {"callId": "c1", "isError": False}})
        self.auditor.handle_event("assistant/chunk", {"run_id": "r1", "text": "hi"})
        self.auditor.handle_event("assistant/message", {"run_id": "r1", "text": "final"})
        self.auditor.handle_event("turn/end", {"run_id": "r1", "reason": "completed"})
        events = [f.event for f in self.auditor.plane.facts("r1")]
        self.assertEqual(events, ["tool/call", "tool/result", "assistant/chunk", "assistant/message", "turn/end"])

    def test_ignores_derived_events(self) -> None:
        self.auditor.handle_event("work_step", {"run_id": "r1", "name": "clock"})
        self.auditor.handle_event("dsh_chunk", {"run_id": "r1", "text": "hi"})
        self.assertEqual(self.auditor.plane.facts("r1"), [])

    def test_ignores_events_without_run_id(self) -> None:
        self.auditor.handle_event("tool/call", {"name": "clock"})
        self.assertEqual(self.auditor.plane.facts("r1"), [])

    def test_run_id_grouping(self) -> None:
        self.auditor.handle_event("tool/call", {"run_id": "rA", "name": "clock"})
        self.auditor.handle_event("tool/call", {"run_id": "rB", "name": "weather"})
        self.assertEqual(self.auditor.plane.facts("rA")[0].payload["name"], "clock")
        self.assertEqual(self.auditor.plane.facts("rB")[0].payload["name"], "weather")
        self.assertEqual(set(self.auditor.runs()), {"rA", "rB"})

    def test_auditor_replay_and_scorecard_convenience(self) -> None:
        self.auditor.handle_event("tool/call", {"run_id": "r1", "name": "clock"})
        self.auditor.handle_event("tool/result", {"run_id": "r1", "message": {"callId": "c1", "name": "clock", "isError": False}})
        self.auditor.handle_event("turn/end", {"run_id": "r1", "reason": "completed"})
        self.assertEqual(len(self.auditor.replay("r1")), 3)
        self.assertEqual(self.auditor.scorecard("r1")["tools_ok"], 1)


class NamingRuleTest(unittest.TestCase):
    def test_no_homerail_naming_in_code(self) -> None:
        hits: list[str] = []
        for p in (ROOT / "backend").rglob("*.py"):
            if "homerail_" in p.read_text(encoding="utf-8"):
                hits.append(str(p))
        self.assertEqual(hits, [])

    def test_audit_files_use_xiao_prefix(self) -> None:
        names = sorted(p.name for p in AUDIT.glob("*.py"))
        self.assertTrue(names)
        for n in names:
            self.assertTrue(n.startswith("xiao_") or n == "__init__.py", n)

    def test_audit_classes_use_xiao_prefix(self) -> None:
        for cls in (XiaoFactPlane, XiaoReplay, XiaoScorecard, XiaoAuditor):
            self.assertTrue(cls.__name__.startswith("Xiao"), cls.__name__)

    def test_fact_event_types_are_bridge_raw(self) -> None:
        self.assertEqual(FACT_EVENT_TYPES, frozenset({"tool/call", "tool/result", "assistant/chunk", "assistant/message", "turn/end"}))


if __name__ == "__main__":
    unittest.main()
