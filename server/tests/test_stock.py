# -*- coding: utf-8 -*-
"""库存进销存测试：商品档案 / 库存变动 / 补货与过期预警

运行：cd server && python -m unittest tests.test_stock -v
"""
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db


class TestStock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "test.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        # 每个用例前清空商品表，保证独立
        with db.get_conn() as conn:
            conn.execute("DELETE FROM products")
            conn.execute("DELETE FROM stock_movements")

    def test_add_and_list_product(self):
        pid = db.add_product("面粉", "原材料", "斤", 50, 20, 2.5, "", "粮油店")
        self.assertIsInstance(pid, int)
        lst = db.list_products()
        self.assertTrue(any(p["id"] == pid and p["name"] == "面粉" for p in lst))

    def test_move_in_out_adj(self):
        pid = db.add_product("可乐", "饮料", "瓶", 0)
        db.move_stock(pid, "in", 100, "进货")
        db.move_stock(pid, "out", 30, "卖掉")
        p = db.list_products()
        row = [x for x in p if x["id"] == pid][0]
        self.assertEqual(row["stock_qty"], 70)
        # 盘点设为 50
        db.move_stock(pid, "adj", 50)
        p = [x for x in db.list_products() if x["id"] == pid][0]
        self.assertEqual(p["stock_qty"], 50)

    def test_out_never_negative(self):
        pid = db.add_product("鸡蛋", "原材料", "个", 5)
        db.move_stock(pid, "out", 99)
        p = [x for x in db.list_products() if x["id"] == pid][0]
        self.assertEqual(p["stock_qty"], 0)

    def test_update_product(self):
        pid = db.add_product("盐", "原材料", "袋", 10)
        self.assertTrue(db.update_product(pid, safety_stock=8, unit_cost=1.5))
        p = [x for x in db.list_products() if x["id"] == pid][0]
        self.assertEqual(p["safety_stock"], 8)
        self.assertEqual(p["unit_cost"], 1.5)

    def test_low_stock_flag(self):
        # 库存 3 <= 安全库存 5 → 触发补货预警
        db.add_product("青菜", "生鲜", "斤", 3, 5, 4, "")
        s = db.stock_summary()
        self.assertTrue(any("青菜" in f and "补货" in f for f in s["flags"]))
        self.assertEqual(s["total_value"], 12)

    def test_expiring_flag(self):
        today = date.today()
        soon = (today + timedelta(days=3)).isoformat()
        db.add_product("酸奶", "生鲜", "盒", 10, 0, 3, soon)
        s = db.stock_summary()
        self.assertTrue(any("酸奶" in f and "快过期" in f for f in s["flags"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)