# -*- coding: utf-8 -*-
"""A3 占位混淆/还原纯函数单元测试（unittest）。

覆盖 contract 要求的：roundtrip、长短 key 冲突、未登记词保留、空 mapping/空文本、幂等。
依赖仅标准库（Python unittest）。本文件 MIT。
"""
import unittest

from backend.gateway.obfuscate import obfuscate, restore, matched_keys


class ObfuscateRoundtripTest(unittest.TestCase):
    def test_roundtrip_two_entities(self):
        mapping = {"我妈": "User_Kinship_Mother", "张总": "User_Leader_Alpha"}
        for text in ("我妈今天找张总", "张总的方案", "我妈和张总都在开会"):
            self.assertEqual(restore(obfuscate(text, mapping), mapping), text, text)

    def test_roundtrip_single(self):
        mapping = {"我妈": "User_Kinship_Mother"}
        self.assertEqual(restore(obfuscate("我妈在吃饭", mapping), mapping), "我妈在吃饭")

    def test_obfuscate_replaces_registered(self):
        mapping = {"我妈": "User_Kinship_Mother", "张总": "User_Leader_Alpha"}
        self.assertEqual(obfuscate("我妈今天找张总", mapping), "User_Kinship_Mother今天找User_Leader_Alpha")


class ObfuscateLongKeyConflictTest(unittest.TestCase):
    def test_long_key_dominates_overlapping_short(self):
        # 「我妈」是「我妈妈」的前缀；长 key 必须先替换，避免短 key 吃掉长 key
        mapping = {"我妈": "User_Kinship_Short", "我妈妈": "User_Kinship_Full"}
        self.assertEqual(obfuscate("我妈妈", mapping), "User_Kinship_Full")
        self.assertEqual(obfuscate("我妈", mapping), "User_Kinship_Short")

    def test_long_key_inside_sentence(self):
        mapping = {"我妈": "User_Kinship_Short", "我妈妈": "User_Kinship_Full"}
        self.assertEqual(obfuscate("我妈妈来看我", mapping), "User_Kinship_Full来看我")
        self.assertEqual(obfuscate("我妈来了", mapping), "User_Kinship_Short来了")

    def test_restore_placeholder_overlap(self):
        # 占位符也存在嵌套前缀（User_Kinship_Short vs Full），还原时也要长占位符先还原
        mapping = {"我妈": "User_Kinship_Short", "我妈妈": "User_Kinship_Full"}
        self.assertEqual(restore("User_Kinship_Full来看我", mapping), "我妈妈来看我")
        self.assertEqual(restore("User_Kinship_Short来了", mapping), "我妈来了")

    def test_matched_keys_long_only(self):
        # 只有长 key 被实际替换时，matched_keys 应只返回长 key（A4 依赖此精确度）
        mapping = {"我妈": "User_Kinship_Short", "我妈妈": "User_Kinship_Full"}
        self.assertEqual(matched_keys("我妈妈来看我", mapping), {"我妈妈"})
        self.assertEqual(matched_keys("我妈来了", mapping), {"我妈"})


class ObfuscateUnregisteredTest(unittest.TestCase):
    def test_unregistered_word_untouched(self):
        mapping = {"我妈": "User_Kinship_Mother"}
        self.assertEqual(obfuscate("王五今天来找我妈", mapping), "王五今天来找User_Kinship_Mother")
        self.assertEqual(restore("User_Kinship_Mother和王五", mapping), "我妈和王五")

    def test_unknown_placeholder_untouched(self):
        mapping = {"我妈": "User_Kinship_Mother"}
        self.assertEqual(restore("这里有个 Other_Entity", mapping), "这里有个 Other_Entity")


class ObfuscateEmptyTest(unittest.TestCase):
    def test_empty_mapping(self):
        self.assertEqual(obfuscate("我妈在开会", {}), "我妈在开会")
        self.assertEqual(restore("我妈在开会", {}), "我妈在开会")

    def test_empty_text(self):
        mapping = {"我妈": "User_Kinship_Mother"}
        self.assertEqual(obfuscate("", mapping), "")
        self.assertEqual(restore("", mapping), "")

    def test_none_text_edge(self):
        # 空/None 之类按空处理；返回原值
        mapping = {"我妈": "User_Kinship_Mother"}
        self.assertEqual(obfuscate("", mapping), "")
        self.assertEqual(obfuscate("", {}), "")


class ObfuscateIdempotenceTest(unittest.TestCase):
    def test_obfuscate_idempotent(self):
        mapping = {"我妈": "User_Kinship_Mother"}
        once = obfuscate("我妈在开会", mapping)
        self.assertEqual(obfuscate(once, mapping), once)

    def test_restore_idempotent(self):
        mapping = {"我妈": "User_Kinship_Mother"}
        once = restore("User_Kinship_Mother在开会", mapping)
        self.assertEqual(restore(once, mapping), once)


if __name__ == "__main__":
    unittest.main()
