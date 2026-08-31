"""M4-M2 穿搭场景验收测试。"""
import unittest

from backend.m4.scene_outfit import (
    OUTFIT_FIELDS,
    LOW_CONFIDENCE_FALLBACK,
    parse_structured,
    has_all_fields,
    build_suggestion,
    render_outfit,
)


class TestParseStructured(unittest.TestCase):
    def test_parse_json(self):
        out = parse_structured('{"单品列表":"白T+牛仔裤","主色调":"蓝","层数":"2"}')
        self.assertEqual(out["主色调"], "蓝")
        self.assertEqual(out["层数"], "2")

    def test_parse_key_value(self):
        out = parse_structured("单品列表: 白T+牛仔裤\n主色调: 蓝\n层数: 2")
        self.assertEqual(out["单品列表"], "白T+牛仔裤")

    def test_parse_failure(self):
        self.assertIsNone(parse_structured("随便聊聊，没有结构化"))


class TestBuildSuggestion(unittest.TestCase):
    def test_structure(self):
        s = build_suggestion({"单品列表": "白T", "主色调": "蓝", "层数": "2"})
        self.assertIn("总评", s)
        self.assertIn("分项", s)
        self.assertIn("可执行建议", s)
        self.assertEqual(set(s["分项"].keys()), {"配色", "层次", "场合匹配"})


class TestRenderOutfit(unittest.TestCase):
    def test_success(self):
        r = render_outfit('{"单品列表":"白T","主色调":"蓝","层数":"2"}')
        self.assertIn("总评", r)

    def test_retry_then_fallback(self):
        r = render_outfit("没有结构化字段")
        self.assertEqual(r["降级"], LOW_CONFIDENCE_FALLBACK)


class TestSceneIsConfig(unittest.TestCase):
    def test_fields_are_config(self):
        self.assertEqual(OUTFIT_FIELDS, ("单品列表", "主色调", "层数"))

    def test_has_all_fields(self):
        self.assertTrue(has_all_fields({"单品列表": "a", "主色调": "b", "层数": "c"}))
        self.assertFalse(has_all_fields({"单品列表": "a"}))


if __name__ == "__main__":
    unittest.main()
