"""M1-C 记忆巩固调度器（云摘要经网关）单元测试。

覆盖 DoD 硬性断言：
1. 出网必过 `guard_outbound`（mock 断言调用，且 LLM 只拿到 guard 后的文本）；
2. 原子提交失败回滚断言（apply_profile 失败 → 保留旧版 + 记 consolidation_pending）；
3. 写锁排他断言（cancelled 旧线程不覆盖：apply_profile 在拿写锁前就拒绝，绝不持久化）；
4. 固结 Prompt 含 JSON Schema `{"summary","emotional_tag","suggested_entities"}`。

测试全部 mock `create_client()`（云 LLM）与 `guard_outbound`（网关），**不上网**；
MemEntry/DataTrack 按 MEMV1_CONTRACT.md 自建最小桩，不硬 import 依赖。
仅标准库；本文件 MIT。
"""
from __future__ import annotations

import copy
import unittest
from unittest import mock

import backend.gateway.gateway as gw
from backend.event_bus import bus
from backend.memv1 import consolidate as cons


# --------------------------------------------------------------------------- #
# 桩：云 LLM / 数据轨 / 画像存储
# --------------------------------------------------------------------------- #
class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self._content = content
        self.messages: list = []
        self.complete_calls = 0

    async def complete(self, messages, tools=None):  # noqa: ANN001
        self.complete_calls += 1
        self.messages = list(messages)
        return _FakeCompletion(self._content)


class _FakeTrack:
    def __init__(self, logs: list[dict]) -> None:
        self._logs = logs

    def items(self, kind: str):  # noqa: ANN001
        return list(self._logs) if kind == "session_logs" else []


class _FakeStore:
    """可控画像存储：可模拟「部分写入后崩溃」以验证原子回滚。"""

    def __init__(self, entries=None, version=0, pending=False, fail_on_save=None):
        self._data = {
            "entries": copy.deepcopy(list(entries or [])),
            "version": int(version),
            "pending": bool(pending),
        }
        self.fail_on_save = fail_on_save
        self.save_calls = 0
        self.restore_calls = 0
        self.saved: list[dict] = []

    def load(self) -> dict:
        return copy.deepcopy(self._data)

    def save(self, profile: dict) -> None:
        self.save_calls += 1
        self.saved.append(copy.deepcopy(profile))
        if self.fail_on_save is not None and self.save_calls >= self.fail_on_save:
            # 部分写入后抛错，模拟中途崩溃，用来验原子回滚。
            self._data = copy.deepcopy(profile)
            raise RuntimeError("disk write failed")
        self._data = copy.deepcopy(profile)

    def restore(self, snapshot: dict) -> None:
        self.restore_calls += 1
        self._data = copy.deepcopy(snapshot)

    def set_pending(self, flag: bool) -> None:
        self._data["pending"] = bool(flag)


# 一条已存在的旧画像条目（原子回滚基准）。
_OLD_ENTRY = {"id": "mem_old", "content": "用户偏好美式"}


def _old_entry() -> dict:
    return copy.deepcopy(_OLD_ENTRY)


# --------------------------------------------------------------------------- #
# Prompt 含 JSON Schema
# --------------------------------------------------------------------------- #
class TestConsolidationPrompt(unittest.TestCase):
    def test_system_prompt_contains_json_schema(self) -> None:
        prompt = cons.build_consolidation_system_prompt()
        self.assertIn("summary", prompt)
        self.assertIn("emotional_tag", prompt)
        self.assertIn("suggested_entities", prompt)

    def test_schema_object_keys_exact(self) -> None:
        self.assertEqual(
            set(cons.CONSOLIDATION_SCHEMA.keys()),
            {"summary", "emotional_tag", "suggested_entities"},
        )

    def test_system_prompt_contains_entity_constraint(self) -> None:
        prompt = cons.build_consolidation_system_prompt()
        self.assertIn("suggested_entities", prompt)
        self.assertIn("疑似新人名", prompt)


# --------------------------------------------------------------------------- #
# 出网必过 guard_outbound（trigger 级）
# --------------------------------------------------------------------------- #
class TestTriggerConsolidation(unittest.TestCase):
    SID = "session_001"

    def _run(self, llm, track, store, *, cancel=None, guard=("cloud_safe", "加工后的文本")):
        with mock.patch.object(gw, "guard_outbound", return_value=guard) as mg, \
                mock.patch.object(gw, "guard_inbound", side_effect=lambda t, s: t):
            result = cons.trigger_consolidation(
                self.SID, data_track=track, store=store,
                llm_client=llm, cancel_token=cancel,
            )
            return result, mg

    def test_guard_outbound_mandatory_before_cloud(self) -> None:
        llm = _FakeLLM('{"summary": "用户近期偏好喝咖啡", "emotional_tag": "3", "suggested_entities": ["张三"]}')
        track = _FakeTrack([{"text": "用户说记得买咖啡"}, {"text": "今天见了张三"}])
        store = _FakeStore()
        result, mg = self._run(llm, track, store)
        mg.assert_called_once_with("用户说记得买咖啡\n今天见了张三", self.SID)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["committed"])
        self.assertEqual(len(result["candidates"]), 2)

    def test_llm_receives_guarded_text_not_raw(self) -> None:
        # DoD 强化：出网后的文本（guard 产物）才允许送云 LLM，原始全文绝不出网。
        llm = _FakeLLM('{"summary": "摘要", "emotional_tag": "2", "suggested_entities": []}')
        track = _FakeTrack([{"text": "原始会话内容"}])
        store = _FakeStore()
        self._run(llm, track, store, guard=("cloud_safe", "混淆后的文本"))
        self.assertEqual(llm.messages[1].content, "混淆后的文本")
        self.assertNotEqual(llm.messages[1].content, "原始会话内容")

    def test_schema_injected_into_system_message(self) -> None:
        llm = _FakeLLM('{"summary": "摘要", "emotional_tag": "1", "suggested_entities": []}')
        track = _FakeTrack([{"text": "xx"}])
        store = _FakeStore()
        self._run(llm, track, store)
        system = llm.messages[0].content
        for key in ("summary", "emotional_tag", "suggested_entities"):
            self.assertIn(key, system)

    def test_blocked_outbound_never_calls_llm(self) -> None:
        llm = _FakeLLM('{"summary": "x"}')
        track = _FakeTrack([{"text": "自伤相关文本"}])
        store = _FakeStore()
        result, _ = self._run(llm, track, store, guard=("blocked", "自伤相关文本"))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(llm.complete_calls, 0)
        self.assertEqual(store.save_calls, 0)

    def test_empty_logs_no_cloud(self) -> None:
        llm = _FakeLLM('{"summary": "x"}')
        result, _ = self._run(llm, _FakeTrack([]), _FakeStore())
        self.assertEqual(result["status"], "empty")
        self.assertEqual(llm.complete_calls, 0)

    def test_cancelled_discards_and_does_not_commit(self) -> None:
        # 用户回来 → cancelled：跑完网络 I/O 但丢弃返回值，绝不提交/覆盖。
        llm = _FakeLLM('{"summary": "旧画像残留", "emotional_tag": "1", "suggested_entities": []}')
        track = _FakeTrack([{"text": "xx"}])
        store = _FakeStore()
        cancel = cons.CancellationToken()
        cancel.cancel()
        result, _ = self._run(llm, track, store, cancel=cancel)
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["committed"])
        self.assertEqual(store.save_calls, 0)


# --------------------------------------------------------------------------- #
# 原子提交 + 写锁排他（apply_profile 级）
# --------------------------------------------------------------------------- #
class TestApplyProfile(unittest.TestCase):
    def _candidate(self, content: str = "新摘要") -> dict:
        return {
            "id": "mem_new", "content": content, "scope": "global", "scope_detail": {},
            "effective_at": "2026-08-30", "source": "inferred", "status": "active",
            "confirmed": False, "affective_luminance": 2, "confidence": 0.6,
            "encrypted": False, "enc_token": "",
        }

    def test_success_commits_and_emits_event(self) -> None:
        store = _FakeStore(entries=[_old_entry()], version=1)
        emitted: list[dict] = []
        unsub = bus.on("memory.profile_updated", lambda p: emitted.append(p))
        self.addCleanup(unsub)
        cons.apply_profile([self._candidate()], store)
        self.assertEqual(store.save_calls, 1)
        self.assertEqual(store.load()["version"], 2)
        self.assertEqual(len(store.load()["entries"]), 2)
        self.assertTrue(any(e["content"] == "新摘要" for e in store.load()["entries"]))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["version"], 2)

    def test_atomic_commit_failure_rolls_back_keeps_old_and_marks_pending(self) -> None:
        # 原子提交失败（store 在写入后抛错）→ 回滚到上一版本 + 记 consolidation_pending。
        store = _FakeStore(entries=[_old_entry()], version=1, fail_on_save=1)
        with self.assertRaises(cons.ConsolidationError):
            cons.apply_profile([self._candidate()], store)
        state = store.load()
        self.assertEqual(state["version"], 1)  # 旧版保留
        self.assertEqual([e["content"] for e in state["entries"]], ["用户偏好美式"])
        self.assertTrue(state["pending"])  # consolidation_pending 标签
        self.assertEqual(store.restore_calls, 1)

    def test_cancelled_apply_profile_never_persists(self) -> None:
        # 写锁排他：cancelled 旧线程返回后，在获取写锁前就被拒绝，绝不持久化/覆盖。
        store = _FakeStore(entries=[_old_entry()], version=1)
        cancel = cons.CancellationToken()
        cancel.cancel()
        with self.assertRaises(cons.ConsolidationCancelled):
            cons.apply_profile([self._candidate()], store, cancel_token=cancel)
        self.assertEqual(store.save_calls, 0)  # 未发生任何持久化写
        self.assertEqual(store.load()["entries"], [_old_entry()])
        self.assertEqual(store.load()["version"], 1)

    def test_non_list_candidates_raises(self) -> None:
        with self.assertRaises(TypeError):
            cons.apply_profile("not a list", _FakeStore())

    def test_uncancelled_apply_profile_ignores_cancel_token(self) -> None:
        # 未取消时 cancel_token 不阻塞：正常提交（写锁排他只是拦「已取消」）+1
        store = _FakeStore(entries=[_old_entry()], version=1)
        cons.apply_profile([self._candidate()], store, cancel_token=cons.CancellationToken())
        self.assertEqual(store.save_calls, 1)


# --------------------------------------------------------------------------- #
# 候选解析（parse_candidates）
# --------------------------------------------------------------------------- #
class TestParseCandidates(unittest.TestCase):
    def test_parse_candidates_from_json(self) -> None:
        text = '{"summary": "用户近期偏好喝咖啡", "emotional_tag": "3", "suggested_entities": ["张三"]}'
        cands = cons.parse_candidates(text)
        self.assertEqual(len(cands), 2)
        self.assertEqual(cands[0]["content"], "用户近期偏好喝咖啡")
        self.assertEqual(cands[0]["affective_luminance"], 3)
        self.assertEqual(cands[0]["source"], "inferred")
        self.assertEqual(cands[1]["content"], "疑似新人名：张三")

    def test_parse_candidates_code_fenced(self) -> None:
        text = '```json\n{"summary": "摘要", "emotional_tag": "4", "suggested_entities": ["李四"]}\n```'
        cands = cons.parse_candidates(text)
        self.assertEqual(len(cands), 2)
        self.assertEqual(cands[1]["content"], "疑似新人名：李四")

    def test_clamp_emotional_tag(self) -> None:
        cands = cons.parse_candidates('{"summary": "s", "emotional_tag": "9", "suggested_entities": []}')
        self.assertEqual(cands[0]["affective_luminance"], 5)

    def test_missing_summary_raises(self) -> None:
        with self.assertRaises(cons.ConsolidationParseError):
            cons.parse_candidates('{"emotional_tag": "1"}')

    def test_invalid_json_raises(self) -> None:
        with self.assertRaises(cons.ConsolidationParseError):
            cons.parse_candidates("不是 JSON")


if __name__ == "__main__":
    unittest.main()
