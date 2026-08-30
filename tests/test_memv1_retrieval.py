"""M1-E 检索注入层 + 呈现轨 180 天滤镜 + 任务态例外（backend/memv1/retrieval.py）单元测试。

覆盖 DoD 三条：
1. >180 天记忆注入不含原始细节（只注入巩固摘要 + 情感高光标签，原始明文硬截断）；
2. 未知动词默认任务态放行（安全默认：不确定 → task）；
3. 沙盒面板降级版本不接 LLM 检索（隔离：升轨明文只进只读面板，不进注入）。

另补：任务态直通（B 型任务检索不被情感滤镜拦截）、新鲜（<=180 天）记忆含原始细节、
分类三态、过期条目剔除、无来源时为空串。

仅标准库；条目按 MEMV1 契约五要素字段自建最小字典桩（不硬 import memv4.MemEntry，
避免与「schema 可能未落地」强耦合）。本文件 MIT。
"""
from __future__ import annotations

import datetime as _dt
import unittest

from backend.memv1 import retrieval


def _entry(content: str, effective_at: str, **kw: object) -> dict:
    """按 MEMV1 契约五要素字段构造最小 dict 桩（额外字段经 kw 透传，如 summary）。"""
    e: dict = {
        "id": kw.pop("id", "mem_x"),
        "content": content,
        "effective_at": effective_at,
        "scope": kw.pop("scope", "event"),
        "source": kw.pop("source", "explicit"),
        "status": kw.pop("status", "active"),
        "affective_luminance": kw.pop("affective_luminance", 0),
        "confidence": kw.pop("confidence", 1.0),
        "confirmed": kw.pop("confirmed", False),
    }
    e.update(kw)  # summary / summary_text 等呈现轨巩固摘要字段
    return e


def _ago(days: int) -> str:
    """返回距离今天 `days` 天的 ISO 日期字符串。"""
    return (_dt.date.today() - _dt.timedelta(days=days)).isoformat()


RECALL_Q = "你还记得我去年 debug 成功那个项目叫什么名字吗"
TASK_Q = "帮我把去年那个项目的报错日志翻出来"


class Aged181DayFilterTest(unittest.TestCase):
    """DoD 1：>180 天记忆注入不含原始细节（呈现轨硬截断）。"""

    def setUp(self) -> None:
        self.entry = _entry(
            "Project_9982_Secret",  # 原始明文（严禁进 LLM）
            _ago(400),
            id="mem_997",
            summary="去年调试成功的项目",  # 巩固摘要（呈现轨）
            affective_luminance=5,
        )
        retrieval.set_entry_provider(lambda: [self.entry])
        self.addCleanup(retrieval.reset_entry_provider)

    def test_recall_injection_contains_summary_but_not_raw(self) -> None:
        inject = retrieval.build_injection(RECALL_Q)
        self.assertIn("去年调试成功的项目", inject)       # 巩固摘要注入
        self.assertIn("Affective_Tags: HIGH_LIGHT_5", inject)  # 情感高光标签
        self.assertNotIn("Project_9982_Secret", inject)  # 原始明文硬截断

    def test_pure_render_injection_with_fixed_today(self) -> None:
        # 纯函数可注入 today，与调用日期无关地复现 180 天滤镜
        fixed_today = _dt.date(2026, 9, 1)
        eff = _dt.date(2025, 8, 1).isoformat()  # 距 2026-09-01 超 180 天
        e = _entry("Project_9982_Secret", eff, summary="只有一个模糊印象", affective_luminance=3)
        out = retrieval.render_injection(RECALL_Q, [e], today=fixed_today)
        self.assertIn("只有一个模糊印象", out)
        self.assertNotIn("Project_9982_Secret", out)

    def test_fresh_under_180_days_keeps_raw_detail(self) -> None:
        # <=180 天：呈现轨正常注入原始内容（不触发滤镜）
        e = _entry("不喝咖啡", _ago(30))
        retrieval.set_entry_provider(lambda: [e])
        out = retrieval.build_injection(RECALL_Q)
        self.assertIn("不喝咖啡", out)


class TaskExceptionTest(unittest.TestCase):
    """DoD 2：未知动词默认任务态放行 + B 型任务检索不被情感滤镜拦截。"""

    def test_unknown_verb_defaults_to_task(self) -> None:
        # 无动词/未知动词 → 原始分类 unknown → 安全默认归一为 task（放行）
        self.assertEqual(retrieval.is_recall_or_task("今天天气不错"), "unknown")
        self.assertEqual(retrieval.resolve_state("unknown"), retrieval.DEFAULT_TASK_STATE)
        self.assertEqual(
            retrieval.resolve_state(retrieval.is_recall_or_task("今天天气不错")),
            "task",
        )

    def test_clear_task_and_recall_not_swapped(self) -> None:
        self.assertEqual(retrieval.is_recall_or_task(TASK_Q), "task")
        self.assertEqual(retrieval.is_recall_or_task(RECALL_Q), "recall")

    def test_task_request_bypasses_aged_filter(self) -> None:
        # EVAL 场景三断言 3：B 型任务检索不走情感滤镜，若被拦截即失败
        e = _entry("Project_9982_Secret", _ago(400), summary="去年调试成功的项目")
        retrieval.set_entry_provider(lambda: [e])
        self.addCleanup(retrieval.reset_entry_provider)
        inject = retrieval.build_injection(TASK_Q)
        self.assertIn("Project_9982_Secret", inject)  # 数据轨直通，保留原始细节

    def test_unknown_verb_injection_is_not_filtered_like_recall(self) -> None:
        # 安全默认的「放行」落到注入层：未知动词按任务态直通，不被 180 天滤镜拦截
        e = _entry("Project_9982_Secret", _ago(400), summary="去年调试成功的项目")
        retrieval.set_entry_provider(lambda: [e])
        self.addCleanup(retrieval.reset_entry_provider)
        inject = retrieval.build_injection("随便说点什么")  # 无动词 → unknown → task
        self.assertIn("Project_9982_Secret", inject)


class SandboxIsolationTest(unittest.TestCase):
    """DoD 3：沙盒面板降级版本不接 LLM 检索（隔离）。"""

    def setUp(self) -> None:
        self.entry = _entry(
            "Project_9982_Secret",
            _ago(200),
            id="rec_987",
            summary="去年调试成功的项目",
            affective_luminance=5,
        )
        # 另加一条正常记录作对照：证明注入管道可用、隔离是精准作用于被隔离那条
        self.control = _entry("用户偏好美式咖啡", _ago(10), id="mem_ctrl")
        self.addCleanup(retrieval.reset_sandboxed)
        self.addCleanup(retrieval.reset_entry_provider)

    def test_sandbox_panel_holds_raw_but_injection_does_not(self) -> None:
        retrieval.mark_sandboxed("rec_987")
        retrieval.set_entry_provider(lambda: [self.entry, self.control])
        panel = retrieval.render_sandbox_panel("原始报错日志：Project_9982_Secret")
        inject = retrieval.build_injection("帮我翻出原始记录")  # 用户请求查看原文明文

        # 屏幕诚实：只读面板携带明文与隔离标记
        self.assertIn(retrieval.SANDBOX_MARKER, panel)
        self.assertIn("Project_9982_Secret", panel)
        # 隔离：明文 / 沙盒标记均不进 LLM 注入
        self.assertNotIn("Project_9982_Secret", inject)
        self.assertNotIn(retrieval.SANDBOX_MARKER, inject)
        # 对照：注入管道仍正常（未隔离条目可注入），证明隔离精准而非整体瘫痪
        self.assertIn("用户偏好美式咖啡", inject)

    def test_sandboxed_id_only_isolated_not_all(self) -> None:
        # 仅被标记 id 隔离，未标记记录照常注入
        retrieval.mark_sandboxed("rec_987")
        retrieval.set_entry_provider(lambda: [self.entry, self.control])
        inject = retrieval.build_injection(TASK_Q)
        self.assertNotIn("Project_9982_Secret", inject)  # 隔离那条不进
        self.assertIn("用户偏好美式咖啡", inject)          # 未隔离那条照常（任务态直通）

    def test_sandbox_marker_is_deterministic_prefix(self) -> None:
        # 降级渲染形式（Markdown 代码块）+ 固定隔离标记，隔离原则不因降级而松
        out = retrieval.render_sandbox_panel("SECRET")
        self.assertTrue(out.startswith(retrieval.SANDBOX_MARKER))
        self.assertIn("```", out)


class ClassificationAndGuardTest(unittest.TestCase):
    """分类三态、无来源空串、过期剔除等守卫。"""

    def test_is_recall_or_task_three_states(self) -> None:
        self.assertEqual(retrieval.is_recall_or_task(TASK_Q), "task")
        self.assertEqual(retrieval.is_recall_or_task(RECALL_Q), "recall")
        self.assertEqual(retrieval.is_recall_or_task("今天天气不错"), "unknown")

    def test_no_provider_returns_empty(self) -> None:
        retrieval.reset_entry_provider()
        self.assertEqual(retrieval.build_injection("随便说说"), "")

    def test_expired_entry_skipped(self) -> None:
        retrieval.set_entry_provider(lambda: [_entry(
            "已过期细节", _ago(400), status="expired", summary="无效"
        )])
        self.addCleanup(retrieval.reset_entry_provider)
        self.assertEqual(retrieval.build_injection(RECALL_Q), "")


if __name__ == "__main__":
    unittest.main()
