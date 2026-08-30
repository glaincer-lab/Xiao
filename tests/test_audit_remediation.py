"""审计 2026-08-29 修复的单元测试（C1/C2/C4/C5 + config_guard）。

只测「无硬件、无弹窗」的纯逻辑路径，避免误启动应用/浏览器。
运行：python -m unittest tests.test_audit_remediation -v
"""
from __future__ import annotations

import asyncio
import unittest

from backend.config_guard import flatten_config, validate_config_updates
from backend.macro_state import build_returning_brief
from backend.audit.xiao_audit import XiaoAuditor
from backend.audit.xiao_fact_plane import XiaoFactPlane


# ---------- C5：TTS 真回退链 ----------
class DummyTTS:
    audio_ext = ".mp3"

    def __init__(self, fail: bool = False, data: bytes = b"a", delay: float = 0) -> None:
        self.fail = fail
        self.data = data
        self.delay = delay
        self.calls = 0

    async def synthesize(self, text: str) -> bytes:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("boom")
        return self.data

    async def speak(self, text: str) -> None:
        raise AssertionError("should not be called in chain.speak")


class TestTTSChain(unittest.TestCase):
    def test_fallback_to_next(self):
        from backend.tts.chain import TTSChain

        chain = TTSChain([DummyTTS(fail=True), DummyTTS(data=b"ok")], timeout=1)
        async def main():
            await chain.speak("hi")  # 不抛；落到第二个
        asyncio.run(main())
        self.assertEqual(len(chain._engines), 2)
        self.assertIsNone(chain._last_error)

    def test_all_fail_does_not_raise(self):
        from backend.tts.chain import TTSChain

        chain = TTSChain([DummyTTS(fail=True), DummyTTS(fail=True)], timeout=1)
        async def main():
            await chain.speak("hi")  # 全部失败也不抛，主流程不被堵死
        asyncio.run(main())
        self.assertIsNotNone(chain._last_error)

    def test_synthesize_returns_first_success(self):
        from backend.tts.chain import TTSChain

        chain = TTSChain([DummyTTS(fail=True), DummyTTS(data=b"ok")], timeout=1)
        async def main():
            return await chain.synthesize("hi")
        self.assertEqual(asyncio.run(main()), b"ok")


# ---------- C4：ASR 真回退链 ----------
class DummyASR:
    def __init__(self, fail: bool = False, text: str = "hello") -> None:
        self.fail = fail
        self.text = text
        self.started = False

    def start(self) -> None:
        self.started = True

    def feed(self, pcm: bytes) -> None:
        pass

    def stop(self) -> str:
        if self.fail:
            raise RuntimeError("boom")
        return self.text

    def close(self) -> None:
        pass


class TestASRChain(unittest.TestCase):
    def test_primary_ok(self):
        from backend.asr.chain import ASRChain

        ch = ASRChain(DummyASR(text="你好"), DummyASR(text="兜底"), timeout=2)
        ch.start()
        ch.feed(b"x")
        self.assertEqual(ch.stop(), "你好")

    def test_primary_fail_fallback(self):
        from backend.asr.chain import ASRChain

        ch = ASRChain(DummyASR(fail=True), DummyASR(text="兜底"), timeout=2)
        ch.start()
        ch.feed(b"x")
        self.assertEqual(ch.stop(), "兜底")

    def test_no_fallback_returns_empty(self):
        from backend.asr.chain import ASRChain

        ch = ASRChain(DummyASR(fail=True), None, timeout=2)
        ch.start()
        ch.feed(b"x")
        self.assertEqual(ch.stop(), "")


# ---------- C1：open_app 白名单 + 审批 ----------
class FakeHook:
    def __init__(self, val: bool) -> None:
        self.val = val
        self.calls = 0

    async def __call__(self, action: str, prompt: str | None = None) -> bool:
        self.calls += 1
        return self.val


class TestOpenApp(unittest.TestCase):
    def test_url_no_approval(self) -> None:
        from backend.tools import open_app

        t = open_app.OpenAppTool()
        self.assertFalse(t._needs_approval("https://www.example.com"))
        self.assertFalse(t._needs_approval("http://x.com/a"))
        self.assertTrue(t._needs_approval("calc"))
        self.assertTrue(t._needs_approval("C:/path/x.exe"))

    def test_whitelist_reject_non_whitelisted(self) -> None:
        from backend.tools import open_app

        t = open_app.OpenAppTool()
        self.assertIn("未授权应用", t._open("cmd"))
        self.assertIn("未授权应用", t._open("C:/definitely/not_exists.exe"))

    def test_hook_missing_rejects(self) -> None:
        from backend.tools import open_app

        open_app.set_confirm_hook(None)
        t = open_app.OpenAppTool()
        async def main():
            return await t.run("calc")
        self.assertIn("不可用", asyncio.run(main()))

    def test_hook_deny_rejects(self) -> None:
        from backend.tools import open_app

        open_app.set_confirm_hook(FakeHook(False))
        t = open_app.OpenAppTool()
        async def main():
            return await t.run("C:/x/a.exe")
        self.assertIn("拒绝", asyncio.run(main()))


# ---------- C2：/api/config 写入守卫 ----------
class TestConfigGuard(unittest.TestCase):
    def test_flatten(self) -> None:
        self.assertEqual(set(flatten_config({"router": {"mode": "auto"}, "agent": {"max_history": 5}})),
                         {"router.mode", "agent.max_history"})

    def test_perms_rejected(self) -> None:
        err = validate_config_updates({"perms": {"standing_grants": ["system"]}})
        self.assertIsNotNone(err)
        self.assertIn("perms", err)
        self.assertIn("standing", err)

    def test_unknown_path_rejected(self) -> None:
        err = validate_config_updates({"foo": {"bar": 1}})
        self.assertIsNotNone(err)
        self.assertIn("未知", err)

    def test_valid_path_allowed(self) -> None:
        self.assertIsNone(validate_config_updates({"agent": {"max_history": 10}}))
        self.assertIsNone(validate_config_updates({"router": {"mode": "auto"}}))


# ===================== 质检 R1/R2/R3 修复验证 =====================

import os
import tempfile
from pathlib import Path


class _AuditBase:
    """为审计测试在项目内可写目录（logs/）建临时基目录，规避受限系统 temp 权限。"""
    def __init__(self) -> None:
        self.base = Path(os.path.join(Path(__file__).resolve().parent.parent, "logs")) / f"audit_r_{os.urandom(4).hex()}"
        self.base.mkdir(parents=True, exist_ok=True)

    def cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)


class TestAuditChunkBatching(unittest.TestCase):
    """R1：审计器对高频 assistant/chunk 限频缓冲（保序、降 I/O），且不破坏即时事件顺序。"""
    def test_chunk_buffered_until_non_chunk_or_threshold(self) -> None:
        a = _AuditBase()
        try:
            auditor = XiaoAuditor(base_dir=str(a.base))
            # 连续 3 个 chunk 不落盘（未达阈值 4KB 前在缓冲中）
            auditor.handle_event("assistant/chunk", {"run_id": "r1", "text": "hi"})
            auditor.handle_event("assistant/chunk", {"run_id": "r1", "text": "there"})
            # 出现非 chunk 事件 → 先冲刷 chunk（保序），再落 message
            auditor.handle_event("assistant/message", {"run_id": "r1", "text": "full"})
            facts = auditor.plane.facts("r1")
            events = [f.event for f in facts]
            self.assertEqual(events, ["assistant/chunk", "assistant/chunk", "assistant/message"])
            # chunk 文本已缓存并保序：两条 chunk + 一条 message
            self.assertEqual(len(facts), 3)
            self.assertEqual(facts[2].payload["text"], "full")
        finally:
            a.cleanup()

    def test_chunk_threshold_flushes(self) -> None:
        a = _AuditBase()
        try:
            auditor = XiaoAuditor(base_dir=str(a.base))
            # 用大文本逼近 4KB 阈值，触发自动刷盘
            big = "x" * 5000
            auditor.handle_event("assistant/chunk", {"run_id": "r1", "text": big})
            facts = auditor.plane.facts("r1")
            self.assertEqual([f.event for f in facts], ["assistant/chunk"])
            self.assertEqual(len(facts), 1)
        finally:
            a.cleanup()


class TestAuditSanitize(unittest.TestCase):
    """R2：审计落盘前经 M0.2 网关本地混淆，隐私文本（人名/关系）被占位符替换。"""
    def test_sensitive_keyword_redacted(self) -> None:
        a = _AuditBase()
        try:
            auditor = XiaoAuditor(base_dir=str(a.base))
            # 命中 compliance.local_only_keywords 的敏感词（如「密码」）→ 落盘前被 [REDACTED] 遮蔽
            auditor.handle_event("assistant/chunk", {"run_id": "r1", "text": "我的数据库密码是 abc123"})
            auditor.flush()
            facts = auditor.plane.facts("r1")
            self.assertEqual(len(facts), 1)
            sanitized = facts[0].payload.get("text", "")
            # 敏感词被遮蔽，绝不明文落盘
            self.assertNotIn("abc123", sanitized)
            self.assertIn("[REDACTED]", sanitized)
        finally:
            a.cleanup()

    def test_non_text_fields_untouched(self) -> None:
        a = _AuditBase()
        try:
            auditor = XiaoAuditor(base_dir=str(a.base))
            auditor.handle_event("tool/result", {"run_id": "r1", "name": "clock", "content": "12:00"})
            facts = auditor.plane.facts("r1")
            self.assertEqual(facts[0].payload["name"], "clock")
            self.assertEqual(facts[0].payload["content"], "12:00")
        finally:
            a.cleanup()


class TestBriefDiversityFuse(unittest.TestCase):
    """R3：简报多样性熔断阀——源 A 为空时源 B 被钳制为 1 条浪漫化表达；源 A 非空照常。"""
    def test_source_a_empty_fuses_source_b_to_one_romantic(self) -> None:
        brief = build_returning_brief(source_a=[], source_b=["任务1", "任务2", "任务3"], source_c=["lore条目"])
        # 源 A 为空 → 源 B 只输出 1 条浪漫化概述（不刷运维日志）
        self.assertEqual(len(brief), 2)
        self.assertIn("数字小任务", brief[0])
        self.assertIn("3", brief[0])  # 3 个任务被概述
        self.assertEqual(brief[1], "lore条目")

    def test_source_a_nonempty_keeps_three_source_order(self) -> None:
        brief = build_returning_brief(source_a=["系统足迹1", "系统足迹2"], source_b=["小二足迹1"], source_c=["lorebook条目1"])
        self.assertEqual(brief, ["系统足迹1", "系统足迹2", "小二足迹1", "lorebook条目1"])


if __name__ == "__main__":
    unittest.main()
