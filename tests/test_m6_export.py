"""M6 §4.3 记忆全量导出/迁移测试：四类资产打包、往返恢复、选择性删除、隐私提示、事件发布。"""
from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid
from pathlib import Path

from backend.config import ROOT
from backend.event_bus import EventBus
from backend.m6.export import EXPORT_VERSION, MemoryExporter
from backend.m6.growth import GrowthStore
from backend.memv1.persona import PersonaStore
from backend.memv4 import DataTrack


def _make_tmp_dir(tag: str) -> Path:
    """在 workspace 的 .tmp 下创建可写临时目录（沙箱拒写系统 Temp，见 test_memv4.py）。"""
    d = ROOT / ".tmp" / f"m6_export_{tag}_{uuid.uuid4().hex[:8]}"
    os.makedirs(d, exist_ok=True)
    return d


def _seed(root: Path) -> None:
    """在 root 下写入九类资产各若干条（成长记录/记忆/画像/人设/人物卡/世界观/哀伤标签/入册状态/纪念锚点）。"""
    g = GrowthStore(root=root)
    g.add_user_record("项目拿下", source="explicit", canon=True)
    g.add_agent_record("第一次自动调温", capability_event="ha_scene_auto")
    g.add_shared_memory("陪你赶工", luminance=5)
    g.set_micro_cooling("feedback", 999999.0)

    t = DataTrack(root=root)
    t.append("session_logs", {"text": "今天聊了搬家"})
    t.append("raw_frames_meta", {"frame": "客厅"})
    t.append("context_snapshots", {"snapshot": "2026"})

    p = PersonaStore(root=root)
    p.update_persona({"identity": "小二·迁移版"})
    p.add_habit("八点起床", category="作息")
    p.add_kinship_card(
        "妈妈", "母亲",
        events=[{"date": "2026-01-01", "title": "生日", "emotion": "温暖"}],
    )
    p.add_grief_tag("爸爸")  # 哀伤标签（persona/grief.json）

    # 世界观：persona/lorebook/*.json，每个文件为 list[dict]（文件名保真）
    lore = root / "persona" / "lorebook"
    os.makedirs(lore, exist_ok=True)
    with (lore / "world.json").open("w", encoding="utf-8") as fh:
        json.dump(
            [{"content": "世界观条目A", "triggers": ["搬家"], "weight": 1}],
            fh, ensure_ascii=False,
        )
    with (lore / "self.json").open("w", encoding="utf-8") as fh:
        json.dump(
            [{"content": "自我认知条目", "triggers": ["小二"]}],
            fh, ensure_ascii=False,
        )

    # 入册状态 / 纪念锚点（M6 顶层状态文件，与 canonize.py / memorial.py 布局一致）
    with (root / "canonizer_state.json").open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "pending": [{"id": "p1", "milestone": "项目拿下"}],
                "cooldowns": {"task.completed": 123456.0},
                "weekly": {"2026-W01": 2},
            },
            fh, ensure_ascii=False,
        )
    with (root / "memorial.json").open("w", encoding="utf-8") as fh:
        json.dump({"last_ask": {"妈妈": "2026-08-01"}}, fh, ensure_ascii=False)


class MemoryExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_tmp_dir("case")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))

    # ---- 验收断言 1：四类资产完整性 ----

    def test_export_contains_four_asset_types(self) -> None:
        root = self._tmp
        _seed(root)
        data = MemoryExporter(
            GrowthStore(root=root), persona_root=root, memv4_root=root
        ).export()
        self.assertEqual(data["version"], EXPORT_VERSION)
        self.assertTrue(data["提示加密"])  # 验收断言 3：隐私提示加密字段
        # 记忆（数据轨三层）
        self.assertEqual(len(data["记忆"]["session_logs"]), 1)
        self.assertEqual(len(data["记忆"]["raw_frames_meta"]), 1)
        self.assertEqual(len(data["记忆"]["context_snapshots"]), 1)
        # 画像（惯例）
        self.assertEqual(len(data["画像"]["habits"]), 1)
        # 人设
        self.assertEqual(data["人设"]["identity"], "小二·迁移版")
        # 人物卡
        self.assertEqual(len(data["人物卡"]), 1)
        self.assertEqual(data["人物卡"][0]["name"], "妈妈")
        # 成长记录三轨
        self.assertEqual(len(data["成长记录"]["user_track"]), 1)
        self.assertEqual(len(data["成长记录"]["agent_track"]), 1)
        self.assertEqual(len(data["成长记录"]["shared_memories"]), 1)

    # ---- 验收断言 4：世界观/哀伤标签/入册状态/纪念锚点 完整性 ----

    def test_export_contains_four_new_asset_types(self) -> None:
        root = self._tmp
        _seed(root)
        data = MemoryExporter(
            GrowthStore(root=root), persona_root=root, memv4_root=root
        ).export()
        # 世界观：文件名→内容 映射保真（不扁平化）
        self.assertEqual(set(data["世界观"].keys()), {"world.json", "self.json"})
        self.assertEqual(data["世界观"]["world.json"][0]["triggers"], ["搬家"])
        self.assertEqual(data["世界观"]["world.json"][0]["weight"], 1)
        self.assertEqual(data["世界观"]["self.json"][0]["content"], "自我认知条目")
        # 哀伤标签
        self.assertEqual([t["entity"] for t in data["哀伤标签"]], ["爸爸"])
        # 入册状态（canonizer_state 布局）
        self.assertEqual(data["入册状态"]["pending"][0]["milestone"], "项目拿下")
        self.assertEqual(data["入册状态"]["cooldowns"], {"task.completed": 123456.0})
        self.assertEqual(data["入册状态"]["weekly"], {"2026-W01": 2})
        # 纪念锚点
        self.assertEqual(data["纪念锚点"], {"last_ask": {"妈妈": "2026-08-01"}})

    def test_export_new_assets_default_when_missing(self) -> None:
        root = self._tmp  # 空目录，无任何种子
        data = MemoryExporter(
            GrowthStore(root=root), persona_root=root, memv4_root=root
        ).export()
        self.assertEqual(data["世界观"], {})  # 空目录给 {}
        self.assertEqual(data["哀伤标签"], [])
        self.assertEqual(
            data["入册状态"], {"pending": [], "cooldowns": {}, "weekly": {}}
        )
        self.assertEqual(data["纪念锚点"], {"last_ask": {}})

    def test_export_roundtrip_restores_four_new_asset_types(self) -> None:
        src = self._tmp / "src"
        dst = self._tmp / "dst"
        os.makedirs(src)
        os.makedirs(dst)
        _seed(src)
        data = MemoryExporter(
            GrowthStore(root=src), persona_root=src, memv4_root=src
        ).export()

        MemoryExporter(
            GrowthStore(root=dst), persona_root=dst, memv4_root=dst
        ).import_data(data)

        # 世界观：逐文件写回，文件名保真
        lore_files = sorted(p.name for p in (dst / "persona" / "lorebook").glob("*.json"))
        self.assertEqual(lore_files, ["self.json", "world.json"])
        with (dst / "persona" / "lorebook" / "world.json").open("r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), data["世界观"]["world.json"])
        with (dst / "persona" / "lorebook" / "self.json").open("r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), data["世界观"]["self.json"])
        # 哀伤标签（PersonaStore 读取验证）
        self.assertEqual(
            [t["entity"] for t in PersonaStore(root=dst).grief_tags()], ["爸爸"]
        )
        # 入册状态 / 纪念锚点：原样写回
        with (dst / "canonizer_state.json").open("r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), data["入册状态"])
        with (dst / "memorial.json").open("r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), data["纪念锚点"])

    def test_import_rejects_invalid_lorebook_entries(self) -> None:
        root = self._tmp
        exp = MemoryExporter(
            GrowthStore(root=root), persona_root=root, memv4_root=root
        )
        with self.assertRaises(ValueError):
            exp.import_data(
                {"version": "1.0", "世界观": {"world.json": "not a list"}}
            )
        with self.assertRaises(ValueError):
            exp.import_data(
                {"version": "1.0", "世界观": {"../escape.json": []}}
            )

    def test_export_roundtrip_restores_all_assets(self) -> None:
        src = self._tmp / "src"
        dst = self._tmp / "dst"
        os.makedirs(src)
        os.makedirs(dst)
        _seed(src)
        src_store = GrowthStore(root=src)
        data = MemoryExporter(src_store, persona_root=src, memv4_root=src).export()

        dst_store = GrowthStore(root=dst)
        MemoryExporter(dst_store, persona_root=dst, memv4_root=dst).import_data(data)

        # 成长记录：数量 / 关键字段 / id 保真
        g2 = GrowthStore(root=dst)
        u = g2.user_records()
        self.assertEqual(len(u), 1)
        self.assertEqual(u[0]["milestone"], "项目拿下")
        self.assertTrue(u[0]["canon"])
        self.assertEqual(u[0]["id"], src_store.user_records()[0]["id"])
        a = g2.agent_records()
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["capability_event"], "ha_scene_auto")
        self.assertEqual(g2.shared_memories()[0]["event"], "陪你赶工")
        self.assertEqual(g2.shared_memories()[0]["luminance"], 5)
        self.assertEqual(
            g2.micro_cooling(),
            {"cooldown_until": 999999.0, "last_type": "feedback"},
        )
        # 记忆：数据轨三层数量与关键字段
        t2 = DataTrack(root=dst)
        self.assertEqual(t2.count("session_logs"), 1)
        self.assertEqual(t2.items("session_logs")[0]["text"], "今天聊了搬家")
        self.assertEqual(t2.items("raw_frames_meta")[0]["frame"], "客厅")
        self.assertEqual(t2.items("context_snapshots")[0]["snapshot"], "2026")
        # 画像 / 人设 / 人物卡
        p2 = PersonaStore(root=dst)
        self.assertEqual(p2.habit_profile()["habits"][0]["content"], "八点起床")
        self.assertEqual(p2.load_persona()["identity"], "小二·迁移版")
        cards = p2.list_kinship_cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].name, "妈妈")
        self.assertEqual(cards[0].events[0]["title"], "生日")

    def test_export_is_json_serializable(self) -> None:
        root = self._tmp
        _seed(root)
        data = MemoryExporter(
            GrowthStore(root=root), persona_root=root, memv4_root=root
        ).export()
        json.dumps(data, ensure_ascii=False)  # 不抛异常即通过

    # ---- 验收断言 2：选择性删除（按 id 删指定条目，其余保留） ----

    def test_forget_memory_removes_only_target(self) -> None:
        root = self._tmp
        t = DataTrack(root=root)
        id1 = t.append("session_logs", {"text": "要删的"})
        id2 = t.append("session_logs", {"text": "要留的"})
        exp = MemoryExporter(
            GrowthStore(root=root), persona_root=root, memv4_root=root
        )
        exp.forget("session_logs", id1)
        # DataTrack 实例为读取时快照，删除后须重新实例化读取（与磁盘同步断言一致）
        rows = DataTrack(root=root).items("session_logs")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], id2)
        # 旧实例的快照不受影响，但磁盘已更新
        self.assertEqual(len(t.items("session_logs")), 2)

    def test_forget_growth_track_refreshes_store(self) -> None:
        root = self._tmp
        store = GrowthStore(root=root)
        r1 = store.add_user_record("要删的", canon=True)
        r2 = store.add_user_record("要留的")
        exp = MemoryExporter(store, persona_root=root, memv4_root=root)
        exp.forget("user_track", r1["id"])
        recs = store.user_records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["id"], r2["id"])

    def test_forget_habits_and_kinship(self) -> None:
        root = self._tmp
        p = PersonaStore(root=root)
        h1 = p.add_habit("旧习惯", category="作息")
        p.add_habit("新习惯", category="作息")
        p.add_kinship_card("爸爸", "父亲")
        exp = MemoryExporter(
            GrowthStore(root=root), persona_root=root, memv4_root=root
        )
        exp.forget("habits", h1["id"])
        exp.forget("kinship", "爸爸")
        self.assertEqual(
            [x["content"] for x in PersonaStore(root=root).habit_profile()["habits"]],
            ["新习惯"],
        )
        self.assertEqual(PersonaStore(root=root).list_kinship_cards(), [])

    def test_forget_unknown_asset_or_missing_id_raises(self) -> None:
        root = self._tmp
        t = DataTrack(root=root)
        t.append("session_logs", {"text": "唯一"})
        exp = MemoryExporter(
            GrowthStore(root=root), persona_root=root, memv4_root=root
        )
        with self.assertRaises(ValueError):
            exp.forget("bogus_layer", "x")
        with self.assertRaises(ValueError):
            exp.forget("session_logs", "not_exist_id")
        with self.assertRaises(ValueError):
            exp.forget("session_logs", "")

    # ---- 事件发布（memory.export_requested 白名单事件） ----

    def test_export_emits_memory_export_requested(self) -> None:
        root = self._tmp
        bus = EventBus()
        received: list[dict] = []
        unsub = bus.on("memory.export_requested", lambda p: received.append(p))
        self.addCleanup(unsub)
        exp = MemoryExporter(
            GrowthStore(root=root), persona_root=root, memv4_root=root, bus=bus
        )
        exp.export()
        self.assertEqual(received, [{"范围": "全量"}])

    def test_export_without_bus_does_not_raise(self) -> None:
        root = self._tmp
        exp = MemoryExporter(
            GrowthStore(root=root), persona_root=root, memv4_root=root
        )
        self.assertIsInstance(exp.export(), dict)

    # ---- 导入容错：人类可读报错 / 缺失段给默认 ----

    def test_import_rejects_invalid_data(self) -> None:
        root = self._tmp
        exp = MemoryExporter(
            GrowthStore(root=root), persona_root=root, memv4_root=root
        )
        with self.assertRaises(ValueError):
            exp.import_data("not a dict")
        with self.assertRaises(ValueError):
            exp.import_data({"记忆": {}})
        with self.assertRaises(ValueError):
            exp.import_data({"version": "", "记忆": {}})

    def test_import_minimal_data_uses_defaults(self) -> None:
        root = self._tmp
        exp = MemoryExporter(
            GrowthStore(root=root), persona_root=root, memv4_root=root
        )
        exp.import_data({"version": "1.0"})
        self.assertEqual(GrowthStore(root=root).user_records(), [])
        self.assertEqual(DataTrack(root=root).count("session_logs"), 0)
        self.assertEqual(PersonaStore(root=root).habit_profile()["habits"], [])
        # 新增 4 段缺省：不落盘（grief 空、lorebook 目录不存在、状态文件不存在）
        self.assertEqual(PersonaStore(root=root).grief_tags(), [])
        self.assertFalse((root / "persona" / "lorebook").exists())
        self.assertFalse((root / "canonizer_state.json").exists())
        self.assertFalse((root / "memorial.json").exists())


if __name__ == "__main__":
    unittest.main()
