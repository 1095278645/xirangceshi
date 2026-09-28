# -*- coding: utf-8 -*-
"""统一保本线接口测试：/api/breakeven/today

钉住的是「输出闭环」而不是重算公式：保本线公式仍在 store.calc_store_model，
这里只验证接口把 today_summary / store_ledger_stats / 店档案拼成一个店主能懂的结论。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db


class BreakevenTodayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "be.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        with db.get_conn() as conn:
            for t in ("transactions", "vouchers", "voucher_entries", "store_profiles"):
                conn.execute(f"DELETE FROM {t}")

    def _client(self):
        import main
        from fastapi.testclient import TestClient
        return TestClient(main.app)

    def test_need_profile_first(self):
        with self._client() as c:
            j = c.get("/api/breakeven/today").json()
        self.assertFalse(j["configured"])
        self.assertIn("保本线", j["text"])
        self.assertIn("单店", j["text"])

    def test_today_gap_against_profile(self):
        db.save_store_profile("测试店", gross_margin=0.5, rent=3000, salary=3000,
                              utilities=0, total_investment=0, cash_on_hand=0,
                              biz_type="餐饮")
        # 固定成本 6000 / 毛利率 0.5 → 保本月销 12000，保本日销 400，目标 520
        db.add_transaction(None, "卖早餐", 500, "income", "主营业务收入")
        with self._client() as c:
            j = c.get("/api/breakeven/today").json()
        self.assertTrue(j["configured"])
        self.assertAlmostEqual(j["today_income"], 500, places=1)
        self.assertAlmostEqual(j["break_even_day"], 400, places=1)
        self.assertAlmostEqual(j["target_day"], 520, places=1)
        self.assertEqual(j["today_gap"], 0)
        self.assertEqual(j["level"], "warn")  # 今天保本，但本月日均还没到“舒服点”
        self.assertIn("舒服点", j["text"])

    def test_danger_when_today_below_break_even(self):
        db.save_store_profile("测试店", gross_margin=0.5, rent=3000, salary=3000,
                              utilities=0, total_investment=0, cash_on_hand=0,
                              biz_type="餐饮")
        db.add_transaction(None, "卖早餐", 300, "income", "主营业务收入")
        with self._client() as c:
            j = c.get("/api/breakeven/today").json()
        self.assertEqual(j["level"], "danger")
        self.assertAlmostEqual(j["today_gap"], 100, places=1)
        self.assertIn("还差 100", j["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
