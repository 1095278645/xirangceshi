# -*- coding: utf-8 -*-
"""P2 加分项测试：同业基准（示例）与「结论可解释」。

运行：cd server && python -m unittest tests.test_ops_extras -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import benchmark
import db


class TestBenchmark(unittest.TestCase):
    def test_levels(self):
        stats = {"gross_margin": 0.10, "daily_revenue": 2000}
        out = benchmark.compare("餐饮", stats, 12)
        by = {i["metric"]: i for i in out["items"]}
        self.assertEqual(by["gross_margin"]["level"], "低于区间")
        self.assertEqual(by["daily_revenue"]["level"], "区间内")
        self.assertEqual(by["avg_ticket"]["level"], "区间内")
        self.assertTrue(by["gross_margin"]["hint"])

    def test_unknown_biz_falls_back_but_flags(self):
        out = benchmark.compare("外星业态", {"gross_margin": 0.6}, None)
        self.assertFalse(out["matched"])
        self.assertEqual(out["biz_type"], benchmark.DEFAULT_BIZ)
        self.assertIn("示例", out["source"])
        self.assertTrue(out["disclaimer"])

    def test_missing_value(self):
        out = benchmark.compare("餐饮", {}, None)
        by = {i["metric"]: i for i in out["items"]}
        self.assertEqual(by["gross_margin"]["level"], "无数据")


class TestExplainApi(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self._tmp.name) / "ex.db"
        db._schema_ready.clear()
        self.addCleanup(db._schema_ready.clear)
        db.init_db()

    def tearDown(self):
        self._tmp.cleanup()

    def test_explain_returns_voucher_legs(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import orders

        cid, _ = db.find_or_create_customer("王阿姨")
        tid, _voucher = db.add_transaction(cid, "两个肉包一杯豆浆", 6,
                                           trans_type="income",
                                           category="主营业务收入",
                                           counterparty="王阿姨")
        app = FastAPI()
        app.include_router(orders.router)
        c = TestClient(app)
        r = c.get(f"/api/orders/{tid}/explain")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("王阿姨", body["explain"])
        self.assertIn("6.00", body["explain"])
        self.assertTrue(body["entries"])
        dirs = {e["direction"] for e in body["entries"]}
        self.assertEqual(dirs, {"debit", "credit"})

    def test_explain_404(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import orders
        app = FastAPI()
        app.include_router(orders.router)
        c = TestClient(app)
        self.assertEqual(c.get("/api/orders/999999/explain").status_code, 404)


class TestBenchmarkApi(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self._tmp.name) / "bench.db"
        db._schema_ready.clear()
        self.addCleanup(db._schema_ready.clear)
        db.init_db()

    def tearDown(self):
        self._tmp.cleanup()

    def test_benchmark_endpoint(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import store

        db.add_transaction(None, "包子", 20, trans_type="income",
                           category="主营业务收入")
        app = FastAPI()
        app.include_router(store.router)
        c = TestClient(app)
        r = c.get("/api/store/benchmark?biz_type=餐饮")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["biz_type"], "餐饮")
        self.assertTrue(body["items"])
        self.assertIn("period", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
