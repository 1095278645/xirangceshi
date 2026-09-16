# -*- coding: utf-8 -*-
"""熟客「常点」字段的更新语义测试。

背景：记账流程会把**本笔**商品名当作 favorite 传进 find_or_create_customer。
早期实现无条件覆盖 / 合并，导致记账一次熟客常点就变一次：
  - 覆盖：常驻的「肉包,豆浆」被一句「两个肉包一杯豆浆」冲掉
  - 合并：句子混进常点列表，几笔之后全是噪音（上限 6 项）
现在约定：常点只在新建客户时学习一次，之后由店主显式维护，
记账流程不再改写它（set_favorite=False）。

运行：cd server && python -m unittest tests.test_customer_favorite -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db


class TestFavoriteSemantics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "fav.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        with db.get_conn() as conn:
            conn.execute("DELETE FROM customers")
            conn.execute("DELETE FROM transactions")

    def test_new_customer_learns_favorite(self):
        """新建客户时用本笔商品做首次学习。"""
        cid, is_new = db.find_or_create_customer("王阿姨", favorite="肉包")
        self.assertTrue(is_new)
        self.assertEqual(db.get_customer(cid)["favorite"], "肉包")

    def test_recording_order_does_not_overwrite_favorite(self):
        """核心回归：记账流程不覆盖已有常点。"""
        cid, _ = db.find_or_create_customer("王阿姨", favorite="肉包,豆浆")
        # 模拟记账流程
        db.find_or_create_customer("王阿姨", favorite="两个肉包一杯豆浆",
                                   set_favorite=False)
        self.assertEqual(db.get_customer(cid)["favorite"], "肉包,豆浆",
                         "已有常点不该被单笔订单的描述覆盖")

    def test_recording_order_does_not_append_noise(self):
        """连续记账也不该把句子堆进常点列表。"""
        cid, _ = db.find_or_create_customer("李叔", favorite="肉包")
        for item in ("两斤猪肉", "三个包子", "一碗粥", "两根油条", "一杯豆浆"):
            db.find_or_create_customer("李叔", favorite=item, set_favorite=False)
        self.assertEqual(db.get_customer(cid)["favorite"], "肉包")

    def test_explicit_update_can_overwrite_and_clear(self):
        """显式操作（新建/编辑客户）仍可覆盖，也应能清空。"""
        cid, _ = db.find_or_create_customer("张叔", favorite="油条")
        db.find_or_create_customer("张叔", favorite="油条,豆腐脑")
        self.assertEqual(db.get_customer(cid)["favorite"], "油条,豆腐脑")
        # 空串要能清掉（早期用 COALESCE(?, favorite) 时清不掉）
        db.find_or_create_customer("张叔", favorite="")
        self.assertEqual(db.get_customer(cid)["favorite"], "")

    def test_tags_still_merge(self):
        """标签仍按合并语义（与常点不同，标签是累积型信息）。"""
        cid, _ = db.find_or_create_customer("赵姐", tags="对面理发店")
        db.find_or_create_customer("赵姐", tags="熟客", set_favorite=False)
        self.assertEqual(db.get_customer(cid)["tags"], "对面理发店,熟客")

    def test_tags_not_duplicated(self):
        cid, _ = db.find_or_create_customer("陈伯", tags="退休")
        db.find_or_create_customer("陈伯", tags="退休,晨练")
        self.assertEqual(db.get_customer(cid)["tags"], "退休,晨练")

    def test_last_visit_refreshed_on_repeat(self):
        """重复建档要刷新 last_visit（列表按它排序）。"""
        cid, _ = db.find_or_create_customer("孙奶奶")
        with db.get_conn() as conn:
            conn.execute("UPDATE customers SET last_visit='2020-01-01' WHERE id=?", (cid,))
        db.find_or_create_customer("孙奶奶", set_favorite=False)
        self.assertNotEqual(db.get_customer(cid)["last_visit"], "2020-01-01")


if __name__ == "__main__":
    unittest.main(verbosity=2)
