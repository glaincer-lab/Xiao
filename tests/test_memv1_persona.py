"""M1-F 人设/世界观/人物卡/哀伤标签/惯例画像（backend/memv1/persona.py）单元测试。

覆盖 DoD 四个断言 + 人设卡版本历史回退：
1. 人设卡：load_persona 返回契约字段；update 递增版本并保存历史；rollback 回退。
2. 亲友人物卡（M1.5）：可增、改、查；纪念锚点（只读记忆）只读 + 见证式询问可关闭。
3. 哀伤标签（M1.6）：只增不推断——唯一入口 add_grief_tag，幂等、无删除、无自动推断。
4. 惯例画像与重建模式（M1.7）：旧惯例失效、新惯例学习中、退出后转正。
5. 世界观（Lorebook）：触发式注入命中返回内容、未命中返回空串；预置条目存在。

仅标准库；MIT。
"""
from __future__ import annotations

import shutil
import unittest
import uuid
from datetime import date
from pathlib import Path

from backend.config import ROOT
from backend.memv1 import persona


def _write_lorebook(root: Path, filename: str, entries: list) -> None:
    """在传入数据根下写入一条世界观条目文件。"""
    import json as _json

    d = root / "persona" / "lorebook"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(_json.dumps(entries, ensure_ascii=False), encoding="utf-8")


class PersonaStoreTest:
    """基类：提供临时数据根并在 setUp/tearDown 中替换模块默认实例。

    临时目录建在项目工作区（ROOT/.tmp）下，用 `Path.mkdir` 创建——受限运行
    环境下 `tempfile.mkdtemp` 建出的目录不可再写子目录（WinError 5），
    而工作区内以 `mkdir` 建的目录可写；此方案在沙箱与普通终端均可跑绿。
    """

    def setUp(self) -> None:
        tmp_base = ROOT / ".tmp"
        tmp_base.mkdir(parents=True, exist_ok=True)
        self.root = tmp_base / ("persona_test_" + uuid.uuid4().hex[:8])
        self.root.mkdir(parents=True, exist_ok=False)
        self._orig_store = persona._store
        persona._store = persona.PersonaStore(root=self.root)

    def tearDown(self) -> None:
        persona._store = self._orig_store
        shutil.rmtree(self.root, ignore_errors=True)


# 继承基类 + unittest.TestCase（把 mixin 组合成真正用作 TestCase 的类型）
class PersonaCardTest(PersonaStoreTest, unittest.TestCase):
    def test_load_persona_has_contract_fields(self) -> None:
        p = persona.load_persona()
        for k in ("identity", "tone", "addressing", "boundaries", "version", "history"):
            self.assertIn(k, p)
        self.assertEqual(p["identity"], "小二")
        self.assertIsInstance(p["version"], int)
        # 读取缺省人设卡后应落盘（persona.json 存在）
        self.assertTrue((self.root / "persona" / "persona.json").exists())

    def test_update_persona_bumps_version_and_keeps_history(self) -> None:
        before = persona.load_persona()
        self.assertEqual(before["version"], 1)
        updated = persona.update_persona({"addressing": "哥", "tone": "简洁"})
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["addressing"], "哥")
        self.assertEqual(len(updated["history"]), 1)
        self.assertEqual(updated["history"][0]["version"], 1)  # 旧版快照入历史

    def test_rollback_persona_restores_previous_version(self) -> None:
        persona.update_persona({"addressing": "哥"})
        rolled = persona.rollback_persona(1)
        self.assertEqual(rolled["addressing"], "老板")  # 回退到默认称呼
        self.assertEqual(rolled["version"], 3)  # 编号继续递增

    def test_rollback_persona_invalid_version_raises(self) -> None:
        with self.assertRaises(ValueError):
            persona.rollback_persona(99)


class KinshipCardTest(PersonaStoreTest, unittest.TestCase):
    def test_add_get_roundtrip(self) -> None:
        persona.add_kinship_card("王五", "老友", events=[{"date": "2026-08-01", "title": "爬山"}], recent="最近升职")
        card = persona.get_kinship_card("王五")
        self.assertIsNotNone(card)
        self.assertEqual(card.name, "王五")
        self.assertEqual(card.relation, "老友")
        self.assertEqual(card.recent, "最近升职")
        self.assertEqual(len(card.events), 1)
        self.assertEqual(card.events[0]["title"], "爬山")

    def test_get_missing_returns_none(self) -> None:
        self.assertIsNone(persona.get_kinship_card("不存在"))

    def test_add_duplicate_raises(self) -> None:
        persona.add_kinship_card("王五", "老友")
        with self.assertRaises(ValueError):
            persona.add_kinship_card("王五", "同事")

    def test_add_empty_name_or_relation_raises(self) -> None:
        with self.assertRaises(ValueError):
            persona.add_kinship_card("", "老友")
        with self.assertRaises(ValueError):
            persona.add_kinship_card("王五", "")

    def test_update_changes_fields(self) -> None:
        persona.add_kinship_card("王五", "老友", recent="升职")
        persona.update_kinship_card("王五", relation="挚友", recent="结婚", events=[{"date": "2026-09-01", "title": "婚礼"}])
        card = persona.get_kinship_card("王五")
        self.assertEqual(card.relation, "挚友")
        self.assertEqual(card.recent, "结婚")
        self.assertEqual(card.events[0]["title"], "婚礼")

    def test_update_disallowed_field_raises(self) -> None:
        persona.add_kinship_card("王五", "老友")
        # name 是主键（lookup key），无法通过 update 变更（Python 层即拒绝/键不可变更）
        with self.assertRaises(ValueError):
            persona.update_kinship_card("王五", birthday="1990")
        with self.assertRaises(ValueError):
            persona.update_kinship_card("王五", relation_typo="x")

    def test_update_missing_card_raises(self) -> None:
        with self.assertRaises(ValueError):
            persona.update_kinship_card("不存在", recent="x")

    def test_list_sorted(self) -> None:
        persona.add_kinship_card("王五", "老友")
        persona.add_kinship_card("李四", "家人")
        names = [c.name for c in persona.list_kinship_cards()]
        self.assertEqual(names, ["李四", "王五"])

    def test_memorial_anchor_readonly_blocks_edit(self) -> None:
        # 丧失关系 → 纪念锚点（只读记忆），见证式询问月级可关闭
        persona.add_kinship_card("外婆", "外婆")
        card = persona.set_memorial_anchor("外婆", True)
        self.assertTrue(card.readonly)
        self.assertTrue(card.memorial_anchor)
        self.assertTrue(card.memorial_ask)
        # 只读记忆禁止内容编辑
        with self.assertRaises(ValueError):
            persona.update_kinship_card("外婆", recent="想你了")
        # 见证式询问可关闭（对只读记忆同样可用）
        c2 = persona.set_memorial_ask("外婆", False)
        self.assertFalse(c2.memorial_ask)


class GriefTagTest(PersonaStoreTest, unittest.TestCase):
    def test_add_grief_tag_adds_once_and_is_idempotent(self) -> None:
        self.assertIsNone(persona.add_grief_tag("外婆"))
        tags = persona.grief_tags()
        self.assertEqual([t["entity"] for t in tags], ["外婆"])
        self.assertEqual(tags[0]["source"], "explicit")
        # 重复打标幂等：不产生重复条目（只增）
        persona.add_grief_tag("外婆")
        persona.add_grief_tag("外婆")
        self.assertEqual(len(persona.grief_tags()), 1)

    def test_add_grief_tag_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            persona.add_grief_tag("")

    def test_add_grief_tag_multiple(self) -> None:
        persona.add_grief_tag("外婆")
        persona.add_grief_tag("宠物毛毛")
        self.assertEqual({t["entity"] for t in persona.grief_tags()}, {"外婆", "宠物毛毛"})

    def test_only_grows_no_remove_surface(self) -> None:
        # 只增：没有删除接口；重读后计数不减
        persona.add_grief_tag("外婆")
        count = len(persona.grief_tags())
        self.assertEqual(len(persona.grief_tags()), count)
        self.assertEqual(len(persona.grief_tags()), count)  # 重读无副作用
        # 模块面不暴露任何移除/清空标签的入口
        no_remove = [n for n in dir(persona) if "grief" in n.lower() and ("remove" in n.lower() or "del" in n.lower() or "clear" in n.lower())]
        self.assertEqual(no_remove, [])

    def test_no_inference_no_side_effect_creates_tag(self) -> None:
        # 不推断：其他操作（注入世界观/加惯例/建人物卡）都不会自动产生哀伤标签
        before = {t["entity"] for t in persona.grief_tags()}
        persona.inject_lorebook("生命")
        persona.add_habit("11 点睡", "作息")
        persona.add_kinship_card("王五", "老友")
        persona.update_persona({"tone": "x"})
        after = {t["entity"] for t in persona.grief_tags()}
        self.assertEqual(after, before)

    def test_grief_phase_windows(self) -> None:
        # 直接写固定 ts，验证 3/7/30 天窗口（只读帮助，供 M2/M3 心跳日程）
        d = self.root / "persona"
        d.mkdir(parents=True, exist_ok=True)
        (d / "grief.json").write_text(
            '[{"entity":"外婆","ts":"2026-01-01T00:00:00","source":"explicit"}]',
            encoding="utf-8",
        )
        self.assertEqual(persona.grief_phase("外婆", now=date(2026, 1, 2)), "d3")
        self.assertEqual(persona.grief_phase("外婆", now=date(2026, 1, 6)), "d7")
        self.assertEqual(persona.grief_phase("外婆", now=date(2026, 1, 10)), "d30")
        self.assertEqual(persona.grief_phase("外婆", now=date(2026, 2, 20)), "past")
        # 未打标返回空串
        self.assertEqual(persona.grief_phase("没人", now=date(2026, 1, 2)), "")


class HabitProfileTest(PersonaStoreTest, unittest.TestCase):
    def test_habit_profile_default(self) -> None:
        hp = persona.habit_profile()
        self.assertEqual(hp["mode"], "normal")
        self.assertEqual(hp["rebuild_reason"], "")
        self.assertEqual(hp["habits"], [])

    def test_add_habit_active_in_normal(self) -> None:
        persona.add_habit("通常 11 点睡", "作息")
        hp = persona.habit_profile()
        self.assertEqual(len(hp["habits"]), 1)
        self.assertEqual(hp["habits"][0]["status"], "active")
        self.assertEqual(hp["habits"][0]["category"], "作息")

    def test_rebuild_mode_expires_old_habits(self) -> None:
        # 重建模式：旧惯例标失效
        persona.add_habit("通常 11 点睡", "作息")
        persona.add_habit("常喝冰美式", "偏好")
        hp = persona.enter_rebuild_mode("搬家")
        self.assertEqual(hp["mode"], "rebuild")
        self.assertEqual(hp["rebuild_reason"], "搬家")
        statuses = {h["content"]: h["status"] for h in hp["habits"]}
        self.assertEqual(statuses["通常 11 点睡"], "expired")
        self.assertEqual(statuses["常喝冰美式"], "expired")

    def test_new_habit_learning_then_exit_promotes(self) -> None:
        persona.add_habit("11 点睡", "作息")
        persona.enter_rebuild_mode("换工作")
        # 重建期间新增惯例 → learning（学习中）
        new = persona.add_habit("9 点半睡", "作息")
        self.assertEqual(new["status"], "learning")
        # 退出重建 → learning 转正为 active
        hp = persona.exit_rebuild_mode()
        self.assertEqual(hp["mode"], "normal")
        self.assertEqual(hp["rebuild_reason"], "")
        statuses = {h["content"]: h["status"] for h in hp["habits"]}
        self.assertEqual(statuses["9 点半睡"], "active")
        self.assertEqual(statuses["11 点睡"], "expired")  # 旧惯例保持失效


class LorebookTest(PersonaStoreTest, unittest.TestCase):
    def test_inject_lorebook_trigger_matches(self) -> None:
        _write_lorebook(self.root, "world.json", [
            {"id": "lw1", "category": "世界观", "triggers": ["生命"], "content": "生命很珍贵。"},
        ])
        _write_lorebook(self.root, "self.json", [
            {"id": "ls1", "category": "自我认知", "triggers": ["你是谁"], "content": "我是小二。"},
        ])
        self.assertIn("生命很珍贵", persona.inject_lorebook("你怎么看待生命"))
        self.assertIn("我是小二", persona.inject_lorebook("你是谁"))

    def test_inject_lorebook_no_match_returns_empty(self) -> None:
        _write_lorebook(self.root, "world.json", [
            {"id": "lw1", "category": "世界观", "triggers": ["生命"], "content": "生命很珍贵。"},
        ])
        self.assertEqual(persona.inject_lorebook("今天天气不错"), "")

    def test_inject_lorebook_empty_trigger_returns_empty(self) -> None:
        _write_lorebook(self.root, "world.json", [
            {"id": "lw1", "category": "世界观", "triggers": ["生命"], "content": "生命很珍贵。"},
        ])
        self.assertEqual(persona.inject_lorebook(""), "")

    def test_shipped_lorebook_seed_present(self) -> None:
        # 预置条目（世界观 / 自我认知）随包发出
        root = Path(__file__).resolve().parent.parent
        lore = root / "persona" / "lorebook"
        self.assertTrue((lore / "world.json").exists())
        self.assertTrue((lore / "self.json").exists())
        # 契约：inject_lorebook 为 str -> str
        self.assertIsInstance(persona.inject_lorebook("你是谁"), str)


if __name__ == "__main__":
    unittest.main()
