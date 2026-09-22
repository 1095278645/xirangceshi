# -*- coding: utf-8 -*-
"""AI 成本/性能看板测试：落库 → 汇总 → 接口。

重点：
  - 汇总口径正确（token 合计、成功率、分位数、成本、每单成本）；
  - 失败调用也计数（否则成功率永远是 100%）；
  - **记录绝不影响主流程**（指标异常被吞掉）；
  - 未知模型不会被"看起来精确"地定价。

运行：cd server && python -m unittest tests.test_metrics -v
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import db_metrics
import metrics


class _Usage:
    def __init__(self, p, c):
        self.prompt_tokens = p
        self.completion_tokens = c
        self.total_tokens = p + c


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self._tmp.name) / "metrics.db"
        db._schema_ready.clear()
        self.addCleanup(db._schema_ready.clear)
        db.init_db()

    def tearDown(self):
        self._tmp.cleanup()


class TestRecordAndSummary(_Base):
    def test_records_and_totals(self):
        for ms in (100, 200, 300, 400):
            db_metrics.record_ai_call("deepseek-v4.1-flash", ms, _Usage(100, 50),
                                      domain="记账解析")
        s = metrics.summarize(7)
        self.assertEqual(s["calls"], 4)
        self.assertEqual(s["tokens"]["prompt"], 400)
        self.assertEqual(s["tokens"]["completion"], 200)
        self.assertEqual(s["tokens"]["total"], 600)
        self.assertEqual(s["failed"], 0)
        self.assertEqual(s["success_rate"], 1.0)
        self.assertEqual(s["latency_ms"]["p50"], 250)   # [100,200,300,400] 中位数
        self.assertEqual(s["latency_ms"]["max"], 400)
        self.assertGreater(s["cost_est_yuan"], 0)
        self.assertEqual(s["by_domain"][0]["name"], "记账解析")

    def test_failed_call_is_counted(self):
        db_metrics.record_ai_call("deepseek-chat", 50, None, ok=False, error="boom")
        s = metrics.summarize(7)
        self.assertEqual(s["calls"], 1)
        self.assertEqual(s["failed"], 1)
        self.assertEqual(s["success_rate"], 0.0)

    def test_p95_and_avg(self):
        for ms in range(1, 101):                       # 1..100
            db_metrics.record_ai_call("deepseek-chat", ms, _Usage(10, 10))
        s = metrics.summarize(7)
        self.assertGreaterEqual(s["latency_ms"]["p95"], 94)
        self.assertLessEqual(s["latency_ms"]["p95"], 100)
        self.assertIn(s["latency_ms"]["avg"], (50, 51))   # 1..100 均值 50.5

    def test_per_order_cost_uses_orders_in_window(self):
        db_metrics.record_ai_call("deepseek-chat", 10, _Usage(1_000_000, 0))
        # 造 2 笔当月记账，使"每单成本"可计算
        with db.get_conn() as conn:
            for _ in range(2):
                conn.execute("INSERT INTO transactions(trans_type, amount, category) "
                             "VALUES('income', 10, '主营业务收入')")
        s = metrics.summarize(30)
        self.assertEqual(s["orders_in_window"], 2)
        self.assertIsNotNone(s["per_order_cost_est_yuan"])
        # 1M prompt tokens × 2 元/1M = 2 元，摊到 2 单 = 1 元/单
        self.assertAlmostEqual(s["per_order_cost_est_yuan"], 1.0, places=3)

    def test_unknown_model_flagged(self):
        db_metrics.record_ai_call("totally-unknown-model", 10, _Usage(1000, 0))
        s = metrics.summarize(7)
        self.assertIn("totally-unknown-model", s["unknown_priced_models"])

    def test_metric_recording_never_raises(self):
        """db_metrics 记录失败必须被吞掉，不能影响调用方。"""
        with mock.patch("db.get_conn", side_effect=RuntimeError("db down")):
            db_metrics.record_ai_call("x", 1, None)   # 不应抛
        db_metrics.record_ai_call("x", -1, None)       # 非法值也不应抛


class TestMetricsApi(_Base):
    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import metrics as metrics_router
        app = FastAPI()
        app.include_router(metrics_router.router)
        return TestClient(app)

    def test_http_summary_and_calls(self):
        db_metrics.record_ai_call("deepseek-chat", 123, _Usage(10, 20), domain="记账解析")
        c = self._client()
        r = c.get("/api/metrics/ai?days=7")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["calls"], 1)
        self.assertEqual(body["tokens"]["total"], 30)
        r2 = c.get("/api/metrics/ai/calls")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(len(r2.json()["calls"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
