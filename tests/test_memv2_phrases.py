"""M2-D 话术库（backend/memv2/phrases.py）单元测试。

覆盖 DoD：
1. load_phrases 可加载、返回契约字段、id 唯一、非空。
2. 必收话术组全部在场——含「记岔了，你提醒得对」。
3. pick 按姿态×情绪×亲密度选变体（确定性），未知姿态走兜底。
4. ban_words 全量可扫。
5. 无 PyYAML 时回退最小解析器，仍返回相同契约。

仅标准库 + 可选 PyYAML；MIT。
"""
from __future__ import annotations

import unittest

from backend.memv2 import phrases as ph

# 必收话术组：「必须出现的模板片段 → 说明」
REQUIRED_FRAGMENTS = {
    "记岔了，你提醒得对": "认错（必收）",
    "后来怎么样了": "留钩子",
    "后来有下文": "留钩子（companion）",
    "想聊聊，还是先静静": "让渡节奏",
    "我陪你安静待会儿": "邀约式印证",
    "这段时间确实不容易": "哀伤期切换",
    "不太确定": "低置信",
    "不允许你这么否定自己": "参考正例（高危）",
}


class LoadPhrasesTest(unittest.TestCase):
    def test_load_nonempty_and_contract_fields(self) -> None:
        ps = ph.load_phrases()
        self.assertIsInstance(ps, list)
        self.assertTrue(len(ps) >= 20)
        for p in ps:
            for key in ("id", "stance", "emotion", "intimacy_range",
                        "skeleton", "template", "ban_words", "variables"):
                self.assertIn(key, p, f"话术缺少字段 {key}: {p}")
        # template 非空
        for p in ps:
            self.assertIsInstance(p["template"], str)
            self.assertTrue(p["template"].strip())

    def test_ids_unique(self) -> None:
        ids = [p["id"] for p in ph.load_phrases()]
        self.assertEqual(len(ids), len(set(ids)), "话术 id 必须唯一")

    def test_required_groups_present(self) -> None:
        ps = ph.load_phrases()
        all_templates = [p["template"] for p in ps]
        for frag, label in REQUIRED_FRAGMENTS.items():
            with self.subTest(group=label, fragment=frag):
                self.assertTrue(
                    any(frag in t for t in all_templates),
                    f"必收话术组缺失: {label}（应含片段「{frag}」）",
                )

    def test_refuse_group_has_three_segment_skeleton(self) -> None:
        ps = ph.load_phrases()
        refuses = [p for p in ps if p["id"].startswith("refuse_")]
        self.assertGreaterEqual(len(refuses), 3, "三段式拒绝需 ≥3 变体")
        self.assertTrue(
            any(p["skeleton"] == ["接纳情绪", "陈述边界", "提供替代"] for p in refuses),
            "至少一条拒绝话术的 skeleton 应为三段式",
        )

    def test_ack_group_has_acknowledge_fragment(self) -> None:
        ps = ph.load_phrases()
        acks = [p for p in ps if p["id"].startswith("ack_misremember_")]
        self.assertGreaterEqual(len(acks), 1)
        self.assertTrue(
            any("记岔了，你提醒得对" in p["template"] for p in acks),
            "认错组应含「记岔了，你提醒得对」",
        )


class PickTest(unittest.TestCase):
    def test_pick_returns_string_for_known_stance(self) -> None:
        for stance in ("steward", "friend", "companion", "advisor", "medical", "emergency"):
            with self.subTest(stance=stance):
                s = ph.pick(stance, "any", 50)
                self.assertIsInstance(s, str)
                self.assertTrue(s)

    def test_pick_varies_by_stance(self) -> None:
        # 不同姿态应选出不同话术（存在姿态路由差异）
        a = ph.pick("friend", "low", 20)
        b = ph.pick("emergency", "high", 10)
        self.assertNotEqual(a, b)

    def test_pick_prefers_exact_emotion_and_intimacy(self) -> None:
        # friend + low + 低亲密度 → 命中 emotion=low 的变体（refuse_scope_01）
        s = ph.pick("friend", "low", 20)
        self.assertTrue(
            "先别急" in s or "不太确定" in s or "不必现在就振作" in s,
            f"低情绪应命中 low 变体，实际: {s}",
        )

    def test_pick_unknown_stance_returns_default(self) -> None:
        self.assertEqual(ph.pick("metadialogue", "any", 50), ph.DEFAULT_PHRASE)

    def test_pick_deterministic(self) -> None:
        self.assertEqual(ph.pick("advisor", "any", 50), ph.pick("advisor", "any", 50))


class BanWordsTest(unittest.TestCase):
    def test_all_ban_words_collects_and_scannable(self) -> None:
        words = ph.all_ban_words()
        self.assertIsInstance(words, list)
        self.assertTrue(words)
        # 全部非空、去重、可扫
        self.assertEqual(len(words), len(set(words)))
        for w in words:
            self.assertTrue(w.strip())
        # 至少含常见禁用词
        self.assertIn("正在为您记录", words)

    def test_ban_words_per_phrase_are_lists(self) -> None:
        for p in ph.load_phrases():
            self.assertIsInstance(p["ban_words"], list, f"ban_words 应为列表: {p['id']}")


class FallbackParserTest(unittest.TestCase):
    """无 PyYAML 环境（注入 ImportError）下，最小解析器仍返回相同契约。"""

    def _force_builtin(self) -> None:
        orig = ph._yaml_safe_load

        def _raise_import(*_a, **_k):
            raise ImportError("no yaml")

        ph._yaml_safe_load = _raise_import
        self.addCleanup(lambda: setattr(ph, "_yaml_safe_load", orig))

    def test_builtin_parser_matches_contract(self) -> None:
        self._force_builtin()
        ps = ph.load_phrases()
        self.assertTrue(ps)
        # 必收「记岔了」依旧在场
        self.assertTrue(any("记岔了，你提醒得对" in p["template"] for p in ps))
        self.assertGreaterEqual(len(ps), 20)
        for p in ps:
            self.assertIn("template", p)
            self.assertIsInstance(p["intimacy_range"], list)
            self.assertEqual(len(p["intimacy_range"]), 2)

    def test_builtin_parser_inline_list_types(self) -> None:
        self._force_builtin()
        from backend.memv2 import phrases as _ph
        # 直接喂一段 schema 样例，验证内联列表/引号/占位符
        sample = (
            "- id: x_01\n"
            "  stance: friend\n"
            "  emotion: any\n"
            "  intimacy_range: [0, 100]\n"
            "  skeleton: [接纳情绪, 陈述边界, 提供替代]\n"
            "  template: \"{emotion_ack}，但这个我确实做不了，{alternative}。\"\n"
            "  ban_words: [正在为您记录, 已存入]\n"
            "  variables: [emotion_ack, alternative]\n"
        )
        items = _ph._parse_minimal_yaml(sample)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["id"], "x_01")
        self.assertEqual(item["intimacy_range"], [0, 100])
        self.assertEqual(item["skeleton"], ["接纳情绪", "陈述边界", "提供替代"])
        self.assertEqual(item["template"], "{emotion_ack}，但这个我确实做不了，{alternative}。")
        self.assertEqual(item["ban_words"], ["正在为您记录", "已存入"])


class IndependentContractTest(unittest.TestCase):
    """不在项目 prompts/ 里，而用独立目录验证 load_phrases 的 directory 参数。"""

    def test_load_from_custom_directory(self) -> None:
        import tempfile
        from pathlib import Path

        # 用工作区内可写目录（沙箱下 tempfile.mkdtemp 不可再写子文件）
        root = ph.PROMPTS_DIR.parent
        tmp = Path(root) / ".tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        d = tmp / "phrase_test_custom"
        d.mkdir(exist_ok=True)
        (d / "one.yaml").write_text(
            "- id: custom_a\n"
            "  stance: friend\n"
            "  emotion: any\n"
            "  intimacy_range: [0, 100]\n"
            "  skeleton: [x]\n"
            "  template: \"{a} 自定义模板 {b}。\"\n"
            "  ban_words: [禁用词A]\n"
            "  variables: [a, b]\n",
            encoding="utf-8",
        )
        try:
            ps = ph.load_phrases(directory=d)
            self.assertEqual(len(ps), 1)
            self.assertEqual(ps[0]["id"], "custom_a")
            self.assertEqual(ps[0]["template"], "{a} 自定义模板 {b}。")
            self.assertEqual(ps[0]["ban_words"], ["禁用词A"])
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
