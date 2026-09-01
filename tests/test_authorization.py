"""T6 · M0 授权中心（backend/authorization.py）单元测试。

覆盖四根硬约定：
1. 授权项默认全关（摄像头/屏幕感知 false；proactivity_level 0）—— 传感默认关（PRODUCT 红线）。
2. 可查看、可撤回（revoke 恢复出厂默认；per_feature 撤回能清空）。
3. 与设置页联动保存（set -> 落盘 config，重读保持一致）。
4. 提权段无法经 /api/config 写（config_guard 因 authorizations 未在 settings_schema 登记而拒绝）。

只测「无硬件、无弹窗」的纯逻辑路径；AuthorizationCenter 注入内存 FakeConfig，不碰真实 config.yaml。
运行：.venv/Scripts/python.exe -m unittest tests.test_authorization -v
"""
from __future__ import annotations

import asyncio
import unittest

from backend.authorization import (
    AUTHORIZATION_ITEMS,
    DEFAULT_AUTHORIZATIONS,
    AuthorizationCenter,
)
from backend.config_guard import flatten_config, validate_config_updates


class FakeConfig:
    """内存版 Config（与 backend.config.Config 同接口），避免测试写真实 config.yaml。"""

    def __init__(self, data: dict | None = None) -> None:
        self._data: dict = data or {}
        self._saved = 0

    def get(self, dotted: str, default=None):
        node: dict = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def update(self, updates: dict) -> None:
        self._merge(self._data, updates)

    def save(self) -> None:
        self._saved += 1

    def _merge(self, base: dict, override: dict) -> None:
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                self._merge(base[k], v)
            else:
                base[k] = v


class TestDefaultsAllOff(unittest.TestCase):
    def test_defaults_all_off(self):
        ac = AuthorizationCenter(FakeConfig())
        state = ac.get()
        # 传感默认关（PRODUCT 红线）
        self.assertIs(state["camera_enabled"], False)
        self.assertIs(state["screen_awareness"], False)
        self.assertEqual(state["proactivity_level"], 0)
        self.assertEqual(state["emergency_passthrough"], [])
        self.assertEqual(state["per_feature"], {})

    def test_registry_is_centered(self):
        keys = {i["key"] for i in AUTHORIZATION_ITEMS}
        self.assertEqual(
            keys,
            {"cloud_asr", "cloud_llm", "cloud_vision", "cloud_tts",
             "clipboard_read", "screen_capture", "camera_enabled",
             "screen_awareness", "emergency_passthrough", "per_feature",
             "proactivity_level", "guard_outbound"},
        )
        # 默认值与登记一致
        self.assertEqual(set(DEFAULT_AUTHORIZATIONS), keys)
        # 除「网关」外的布尔项默认全关；guard_outbound 是保护措施，默认开
        for item in AUTHORIZATION_ITEMS:
            if item["type"] == "bool" and item["key"] != "guard_outbound":
                self.assertIs(item["default"], False)
        self.assertEqual(DEFAULT_AUTHORIZATIONS["proactivity_level"], 0)
        self.assertIs(DEFAULT_AUTHORIZATIONS["guard_outbound"], True)

    def test_get_returns_copies(self):
        ac = AuthorizationCenter(FakeConfig({"authorizations": {"emergency_passthrough": ["火警"]}}))
        state = ac.get()
        state["emergency_passthrough"].append("地震")  # 不该污染内部
        self.assertEqual(ac.get()["emergency_passthrough"], ["火警"])


class TestViewAndRevoke(unittest.TestCase):
    def test_set_and_view(self):
        cfg = FakeConfig()
        ac = AuthorizationCenter(cfg)
        ac.set("camera_enabled", True)
        self.assertIs(ac.get()["camera_enabled"], True)
        self.assertIs(ac.get_item("camera_enabled"), True)
        self.assertTrue(ac.is_granted("camera_enabled"))
        self.assertEqual(cfg._data["authorizations"]["camera_enabled"], True)

    def test_revoke_resets_default(self):
        ac = AuthorizationCenter(FakeConfig())
        ac.set("screen_awareness", True)
        self.assertIs(ac.get()["screen_awareness"], True)
        ac.revoke("screen_awareness")
        self.assertIs(ac.get()["screen_awareness"], False)
        self.assertFalse(ac.is_granted("screen_awareness"))

    def test_revoke_int_to_zero(self):
        ac = AuthorizationCenter(FakeConfig())
        ac.set("proactivity_level", 60)
        self.assertEqual(ac.get()["proactivity_level"], 60)
        ac.revoke("proactivity_level")
        self.assertEqual(ac.get()["proactivity_level"], 0)

    def test_revoke_per_feature_clears(self):
        cfg = FakeConfig()
        ac = AuthorizationCenter(cfg)
        ac.set_feature("screen_look", True)
        ac.set_feature("computer_mouse", True)
        self.assertTrue(ac.is_feature_granted("screen_look"))
        self.assertTrue(ac.is_feature_granted("computer_mouse"))
        # 撤回重设为 {}（整段替换，验证深合并不会残留）
        ac.revoke("per_feature")
        self.assertEqual(ac.get()["per_feature"], {})
        self.assertFalse(ac.is_feature_granted("screen_look"))

    def test_unknown_item_rejected(self):
        ac = AuthorizationCenter(FakeConfig())
        with self.assertRaises(ValueError):
            ac.set("bogus", True)
        with self.assertRaises(ValueError):
            ac.revoke("bogus")
        with self.assertRaises(ValueError):
            ac.is_granted("bogus")


class TestPerFeature(unittest.TestCase):
    def test_set_feature(self):
        ac = AuthorizationCenter(FakeConfig())
        ac.set_feature("screen_look", True)
        self.assertTrue(ac.is_feature_granted("screen_look"))
        self.assertTrue(ac.get()["per_feature"]["screen_look"])

    def test_set_feature_false_revoke_feature(self):
        ac = AuthorizationCenter(FakeConfig())
        ac.set_feature("screen_look", True)
        ac.revoke_feature("screen_look")
        self.assertFalse(ac.is_feature_granted("screen_look"))

    def test_revoke_full_per_feature_dict(self):
        ac = AuthorizationCenter(FakeConfig())
        ac.set("per_feature", {"a": True, "b": False})
        self.assertTrue(ac.is_feature_granted("a"))
        self.assertFalse(ac.is_feature_granted("b"))


class TestValidation(unittest.TestCase):
    def test_bool_type(self):
        ac = AuthorizationCenter(FakeConfig())
        with self.assertRaises(ValueError):
            ac.set("camera_enabled", "yes")
        with self.assertRaises(ValueError):
            ac.set("camera_enabled", 1)  # int 不是 bool

    def test_int_range_and_type(self):
        ac = AuthorizationCenter(FakeConfig())
        with self.assertRaises(ValueError):
            ac.set("proactivity_level", 200)
        with self.assertRaises(ValueError):
            ac.set("proactivity_level", -1)
        with self.assertRaises(ValueError):
            ac.set("proactivity_level", True)  # bool 是 int 子类，须拒绝
        with self.assertRaises(ValueError):
            ac.set("proactivity_level", "high")

    def test_list_type(self):
        ac = AuthorizationCenter(FakeConfig())
        with self.assertRaises(ValueError):
            ac.set("emergency_passthrough", "火警")
        with self.assertRaises(ValueError):
            ac.set("emergency_passthrough", [1, 2])
        self.assertEqual(ac.set("emergency_passthrough", ["火警"])["emergency_passthrough"], ["火警"])

    def test_dict_type(self):
        ac = AuthorizationCenter(FakeConfig())
        with self.assertRaises(ValueError):
            ac.set("per_feature", ["x"])
        with self.assertRaises(ValueError):
            ac.set("per_feature", {"a": "yes"})  # 值须为 bool
        self.assertIs(ac.set("per_feature", {"a": True})["per_feature"]["a"], True)


class TestConfigGuardRejectsAuthorizations(unittest.TestCase):
    def test_authorizations_rejected_via_config(self):
        # 提权段无法经 /api/config 写：authorizations.* 未在 settings_schema 登记 -> 未知路径拒绝
        err = validate_config_updates({"authorizations": {"camera_enabled": True}})
        self.assertIsNotNone(err)
        self.assertIn("未知", err)

    def test_authorizations_nested_rejected(self):
        err = validate_config_updates({"authorizations": {"per_feature": {"screen_look": True}}})
        self.assertIsNotNone(err)
        self.assertIn("未知", err)

    def test_flatten_authorizations(self):
        self.assertIn("authorizations.camera_enabled",
                      set(flatten_config({"authorizations": {"camera_enabled": True}})))


class TestSaveLinkage(unittest.TestCase):
    def test_set_persists_and_rereads(self):
        # 与设置页联动保存：set -> config.save(); 新实例重读保持一致
        cfg = FakeConfig()
        ac = AuthorizationCenter(cfg)
        ac.set("screen_awareness", True)
        self.assertGreaterEqual(cfg._saved, 1)
        self.assertEqual(cfg._data["authorizations"]["screen_awareness"], True)
        # 重新构造（模拟进程重启后从 config 重读）
        ac2 = AuthorizationCenter(cfg)
        self.assertIs(ac2.get()["screen_awareness"], True)

    def test_revoke_persists_off(self):
        cfg = FakeConfig()
        ac = AuthorizationCenter(cfg)
        ac.set("camera_enabled", True)
        ac.revoke("camera_enabled")
        self.assertEqual(cfg._data["authorizations"]["camera_enabled"], False)


class TestEndpoints(unittest.TestCase):
    """直接在 HTTP 处理层调用 /api/authorizations 端点逻辑（不拉起整机运行）。

    注入内存 FakeConfig 的 AuthorizationCenter，避免写真实 config.yaml。
    """

    def _authorized_main(self):
        import backend.main as m
        m.authorizations = AuthorizationCenter(FakeConfig())
        return m

    def test_get_set_revoke_roundtrip(self):
        m = self._authorized_main()
        async def main():
            # 默认全关
            r = await m.get_authorizations()
            self.assertTrue(r["ok"])
            self.assertIs(r["authorizations"]["camera_enabled"], False)
            self.assertIn("items", r)
            # 设置（联动保存）
            r = await m.set_authorization({"key": "camera_enabled", "value": True})
            self.assertTrue(r["ok"])
            self.assertIs(r["authorizations"]["camera_enabled"], True)
            # 再查看
            self.assertIs((await m.get_authorizations())["authorizations"]["camera_enabled"], True)
            # 撤回
            r = await m.revoke_authorization({"key": "camera_enabled"})
            self.assertTrue(r["ok"])
            self.assertIs(r["authorizations"]["camera_enabled"], False)
        asyncio.run(main())

    def test_endpoint_rejects_invalid(self):
        m = self._authorized_main()
        async def main():
            r = await m.set_authorization({"key": "camera_enabled", "value": "yes"})
            self.assertFalse(r["ok"])
            self.assertIn("布尔", r["msg"])
            r = await m.set_authorization({"key": "bogus", "value": True})
            self.assertFalse(r["ok"])
            self.assertIn("未知授权项", r["msg"])
        asyncio.run(main())

    def test_set_feature_endpoint(self):
        m = self._authorized_main()
        async def main():
            r = await m.set_feature({"feature": "screen_look", "granted": True})
            self.assertTrue(r["ok"])
            self.assertTrue(r["authorizations"]["per_feature"]["screen_look"])
        asyncio.run(main())


if __name__ == "__main__":
    unittest.main()
