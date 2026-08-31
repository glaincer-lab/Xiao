"""M1 向量召回（retrieval 四因子 + 降级兜底）单元测试。

覆盖：
1. 四因子加权排序（语义/关键词/近因/重要度）正确；
2. build_injection 经 set_vector_retriever 走向量召回；
3. 降级：未注入召回器 / 召回器返回 None / 召回器抛异常 → 全量注入现状。
"""
from __future__ import annotations

import datetime as _dt
import unittest

from backend.memv1 import retrieval


def _today() -> str:
    return _dt.date.today().isoformat()


def _fresh_entry(content: str, rid: str = "m1") -> dict:
    return {"id": rid, "content": content, "effective_at": _today(), "status": "active"}


class RankEntriesTest(unittest.TestCase):
    def _cand(self, rid: str, text: str, score: float, ts: float, importance: float | None = None, kind: str | None = None) -> dict:
        meta = {"importance": importance if importance is not None else 0.5}
        if kind:
            meta["kind"] = kind
        return {"id": rid, "text": text, "meta": meta, "ts": ts, "score": score}

    def test_semantic_dominates_when_others_equal(self) -> None:
        today = _dt.date(2026, 1, 1)
        ts = _dt.datetime(2025, 12, 1).timestamp()
        cands = [
            self._cand("low", "北京天气", 0.3, ts),
            self._cand("high", "北京天气", 0.9, ts),
            self._cand("mid", "北京天气", 0.6, ts),
        ]
        ranked = retrieval.rank_entries("北京天气", cands, today=today)
        self.assertEqual(ranked[0]["id"], "high")

    def test_keyword_breaks_semantic_tie(self) -> None:
        today = _dt.date(2026, 1, 1)
        ts = _dt.datetime(2025, 12, 1).timestamp()
        cands = [
            self._cand("match", "包含目标词", 0.5, ts),
            self._cand("nomatch", "完全不相关", 0.5, ts),
        ]
        w = {"semantic": 0.0, "keyword": 1.0, "recency": 0.0, "importance": 0.0}
        ranked = retrieval.rank_entries("目标词", cands, today=today, weights=w)
        self.assertEqual(ranked[0]["id"], "match")

    def test_recency_and_importance_weighted(self) -> None:
        today = _dt.date(2026, 1, 1)
        old_ts = _dt.datetime(2020, 1, 1).timestamp()
        new_ts = _dt.datetime(2025, 12, 31).timestamp()
        cands = [
            self._cand("old_important", "x", 0.0, old_ts, importance=1.0),
            self._cand("new_unimportant", "x", 0.0, new_ts, importance=0.0),
        ]
        w_imp = {"semantic": 0.0, "keyword": 0.0, "recency": 0.0, "importance": 1.0}
        self.assertEqual(retrieval.rank_entries("x", cands, today=today, weights=w_imp)[0]["id"], "old_important")
        w_rec = {"semantic": 0.0, "keyword": 0.0, "recency": 1.0, "importance": 0.0}
        self.assertEqual(retrieval.rank_entries("x", cands, today=today, weights=w_rec)[0]["id"], "new_unimportant")

    def test_top_k_caps(self) -> None:
        today = _dt.date(2026, 1, 1)
        ts = _dt.datetime(2025, 12, 1).timestamp()
        cands = [self._cand(f"c{i}", "text", float(i) / 10, ts) for i in range(10)]
        ranked = retrieval.rank_entries("text", cands, today=today, top_k=3)
        self.assertEqual(len(ranked), 3)


class VectorRecallInjectionTest(unittest.TestCase):
    def tearDown(self) -> None:
        retrieval.reset_vector_retriever()
        retrieval.reset_entry_provider()

    def test_no_retriever_falls_back_to_full_injection(self) -> None:
        retrieval.set_entry_provider(lambda: [_fresh_entry("记得内容")])
        out = retrieval.build_injection("帮我查找")
        self.assertIn("记得内容", out)

    def test_retriever_returns_none_falls_back(self) -> None:
        retrieval.set_entry_provider(lambda: [_fresh_entry("全量兜底")])
        retrieval.set_vector_retriever(lambda q: None)
        out = retrieval.build_injection("帮我查找")
        self.assertIn("全量兜底", out)

    def test_retriever_raises_falls_back(self) -> None:
        retrieval.set_entry_provider(lambda: [_fresh_entry("异常兜底")])

        def boom(q: str):
            raise RuntimeError("embedding 崩了")

        retrieval.set_vector_retriever(boom)
        out = retrieval.build_injection("帮我查找")
        self.assertIn("异常兜底", out)

    def test_vector_recall_path_injects_top_hits(self) -> None:
        retrieval.reset_entry_provider()  # 不设 provider，证明走的是召回路径

        def retriever(q: str):
            return [{
                "id": "v1",
                "text": "向量召回的记忆",
                "meta": {"effective_at": _today(), "status": "active"},
                "ts": _dt.datetime.now().timestamp(),
                "score": 0.9,
            }]

        retrieval.set_vector_retriever(retriever)
        out = retrieval.build_injection("帮我查找向量召回的记忆")
        self.assertIn("向量召回的记忆", out)


if __name__ == "__main__":
    unittest.main()
