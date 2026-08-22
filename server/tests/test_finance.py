# -*- coding: utf-8 -*-
"""资金健康域测试：预算 / 应收应付账龄 / 现金流滚动预测

运行：cd server && python -m unittest tests.test_finance -v
"""
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import finance
from finance import forecast_cashflow, month_key


class TestForecastEngine(unittest.TestCase):
    """现金流滚动预测引擎（纯函数）"""

    def test_steady_cashflow(self):
        r = forecast_cashflow(cash_on_hand=10000, base_income=5000,
                              base_expense=3000, months=6)
        self.assertEqual(len(r["months"]), 6)
        self.assertEqual(r["months"][0]["end_balance"], 12000)  # 10000+2000
        self.assertEqual(r["months"][-1]["end_balance"], 22000)
        self.assertTrue(all(m["safe"] for m in r["months"]))
        self.assertIn("现金流是稳的", r["summary"])

    def test_danger_forecast(self):
        r = forecast_cashflow(cash_on_hand=500, base_income=1000,
                              base_expense=2000, months=3)
        self.assertLess(r["months"][2]["end_balance"], 0)
        self.assertTrue(any("垫钱" in f for f in r["flags"]))

    def test_debt_flow_applied(self):
        # 第2个月有应付到期 5000 → 该月支出变多
        r = forecast_cashflow(cash_on_hand=20000, base_income=3000,
                              base_expense=2000, months=3,
                              debt_flows=[{"month": None, "net": -5000}])
        # 全部归到当月：第一个月 net = 3000-2000-5000 = -4000
        self.assertEqual(r["months"][0]["net"], -4000)

    def test_safety_buffer_warning(self):
        r = forecast_cashflow(cash_on_hand=1000, base_income=1000,
                              base_expense=900, months=6, safety_buffer=900)
        # 期末 1000 元 >= 900 安全垫，不应告警
        self.assertTrue(all(m["enough"] for m in r["months"]))

    def test_month_shift(self):
        self.assertEqual(finance.shift_month("2026-01", -1), "2025-12")
        self.assertEqual(finance.shift_month("2026-12", 1), "2027-01")


class TestBudget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "test.db"
        db.init_db()
        today = date.today()
        cls.month = today.strftime("%Y-%m")
        d = today.isoformat()
        # 一笔支出 8000，一笔收入 2000
        with db.get_conn() as conn:
            conn.executemany(
                "INSERT INTO transactions(trans_type, category, item, amount, created_at) "
                "VALUES(?,?,?,?,?)",
                [("expense", "进货", "进货", 8000, f"{d} 08:00:00"),
                 ("income", "主营业务收入", "卖货", 2000, f"{d} 09:00:00")])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_save_and_list_budget(self):
        bid = db.save_budget(self.month, "expense", 6000, "进货", "计划")
        self.assertIsInstance(bid, int)
        lst = db.list_budgets(self.month)
        self.assertTrue(any(b["id"] == bid and b["amount"] == 6000 for b in lst))

    def test_budget_vs_actual_over(self):
        # 计划支出 6000，实际支出 8000 → 超支
        db.save_budget(self.month, "expense", 6000, "", "计划")
        r = db.budget_vs_actual(self.month)
        self.assertEqual(r["actual"]["expense"], 8000)
        self.assertEqual(r["diff"]["expense"], 2000)
        self.assertTrue(any("超了" in f for f in r["flags"]))

    def test_delete_budget(self):
        bid = db.save_budget("2099-01", "expense", 1)
        self.assertTrue(db.delete_budget(bid))
        self.assertFalse(db.delete_budget(bid))


class TestDebt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "test.db"
        db.init_db()
        past = (date.today() - timedelta(days=100)).isoformat()
        cls.receivable_id = db.add_debt("老客户王姐", "receivable", 3000, past, "赊账")
        cls.payable_id = db.add_debt("批发商", "payable", 2000, date.today().isoformat(), "")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_aging_buckets(self):
        r = db.aging_summary()
        rec = [d for d in r["receivable"] if d["id"] == self.receivable_id][0]
        self.assertEqual(rec["aging_bucket"], "90天以上")
        # 100 天应收 → 触发催收提醒
        self.assertTrue(any("王姐" in f and "催" in f for f in r["flags"]))

    def test_settle_full(self):
        r = db.settle_debt(self.receivable_id)
        self.assertEqual(r["status"], "settled")
        self.assertEqual(r["balance"], 0)

    def test_settle_partial(self):
        r = db.settle_debt(self.payable_id, 800)
        self.assertEqual(r["status"], "open")
        self.assertEqual(r["balance"], 1200)
        db.settle_debt(self.payable_id)  # 清掉，避免影响其他用例

    def test_debt_month_flows_shape(self):
        flows = db.debt_month_flows(6)
        self.assertEqual(len(flows), 6)
        self.assertTrue(all("month" in f and "net" in f for f in flows))


class TestCashflowForecastEntry(unittest.TestCase):
    """现金流预测入口 cashflow_forecast（db_finance）回归：曾因传参名
    debt_receives≠debt_flows 而崩溃，此处确保入口可正常调用并返回预测"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "test.db"
        db.init_db()
        cls.receivable_id = db.add_debt("王姐", "receivable", 3000,
                                        date.today().isoformat(), "赊账")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_cashflow_forecast_runs(self):
        from db_finance import cashflow_forecast
        r = cashflow_forecast(cash_on_hand=50000, months=6)
        self.assertEqual(len(r["months"]), 6)
        self.assertEqual(r["start_cash"], 50000)
        self.assertIsInstance(r["summary"], str)

    def test_cashflow_forecast_includes_debt(self):
        # 当月有应收到期 3000 → 首月流入含该笔，现金流比无应收时更宽裕
        from db_finance import cashflow_forecast
        r = cashflow_forecast(cash_on_hand=50000, months=6)
        self.assertGreaterEqual(r["months"][0]["inflow"], 3000)


if __name__ == "__main__":
    unittest.main(verbosity=2)