"""审计 2026-08-29 修复的单元测试（C1/C2/C4/C5 + config_guard）。

只测「无硬件、无弹窗」的纯逻辑路径，避免误启动应用/浏览器。
运行：python -m unittest tests.test_audit_remediation -v
"""
from __future__ import annotations

import asyncio
import unittest

from backend.config_guard import flatten_config, validate_config_updates


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


if __name__ == "__main__":
    unittest.main()
