# -*- coding: utf-8 -*-
"""冒烟测试：金额提取 / 分类兜底 / 复式记账 / 月度汇总（无需启动服务器）

运行：cd server && python -m unittest tests.test_smoke -v
"""
import re
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from ai import _extract_amount
from categories import detect_category, is_known_category


class TestAmountExtract(unittest.TestCase):
    """L6 变换管道：大白话 → 金额 提取正确性"""

    def test_arabic_numeral(self):
        self.assertEqual(_extract_amount("王阿姨买了两个肉包，6块"), 6.0)
        self.assertEqual(_extract_amount("房租一共2500"), 2500.0)
        self.assertEqual(_extract_amount("花了1200进货"), 1200.0)

    def test_chinese_numeral(self):
        self.assertEqual(_extract_amount("一百二十块"), 120.0)
        self.assertEqual(_extract_amount("花了三千五"), 3500.0)
        self.assertEqual(_extract_amount("房租一共两千五"), 2500.0)
        self.assertEqual(_extract_amount("收了八百"), 800.0)

    def test_no_amount(self):
        self.assertIsNone(_extract_amount("两个肉包"))       # 不把'两个'误判为金额
        self.assertIsNone(_extract_amount("今天生意不错"))


class TestCategory(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(detect_category("今天进了一批货"), ("进货", "expense"))
        self.assertEqual(detect_category("房租水电要交了"), ("租赁及物业费", "expense"))
        self.assertEqual(detect_category("卖了两碗馄饨"), ("主营业务收入", "income"))

    def test_known(self):
        self.assertTrue(is_known_category("进货"))
        self.assertFalse(is_known_category("餐饮"))          # AI 乱给分类时必须能被拦住


class TestLedger(unittest.TestCase):
    """L7 闭环：记账 → 凭证 → 汇总 全链路正确性（临时库，不污染真实数据）"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "test.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_transaction_with_voucher(self):
        _, v = db.add_transaction(None, "进了一批货", 120, "expense", "进货")
        self.assertIsNotNone(v)
        self.assertRegex(v["voucher_no"], r"^记-\d{6}-\d{3}$")
        vs = db.list_vouchers(1)
        entries = vs[0]["entries"]
        self.assertEqual(len(entries), 2)                    # 借贷两笔分录
        self.assertEqual(sum(e["amount"] for e in entries), 240.0)  # 借贷平衡

    def test_voucher_no_embeds_period(self):
        """凭证号含月份前缀：voucher_no 全局 UNIQUE，否则每月从 1 重新编号会跨月撞号"""
        _, v = db.add_transaction(None, "测试跨月", 1, "income", "主营业务收入")
        period = date.today().isoformat()[:7].replace("-", "")
        self.assertTrue(v["voucher_no"].startswith(f"记-{period}-"))

    def test_transaction_without_amount(self):
        _, v = db.add_transaction(None, "王阿姨买了包子，没记账单", None, "income", "主营业务收入")
        self.assertIsNone(v)                                 # 无金额不生成 0 元空凭证
        s = db.today_summary()
        self.assertGreaterEqual(s["cnt"], 1)

    def test_monthly_friendly_names(self):
        m = db.monthly_summary()
        for c in m["categories"]:
            self.assertNotRegex(c["friendly"], r"^\d+$")     # friendly 不允许是科目代码


if __name__ == "__main__":
    unittest.main(verbosity=2)