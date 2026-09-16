# -*- coding: utf-8 -*-
"""经营洞察 / 报税建议的缓存测试。

背景：这两块都在页面操作时被自动触发，原实现每次都真调 AI：
  - 经营洞察：账本页默认标签是流水，进页面即请求（20~30 秒 + 每次计费）
  - 报税建议：点「算增值税」后自动请求（30~65 秒）
而且两者的缓存键都不区分维度（洞察不区分月份、建议不区分销售额），
会把上一次的结果显示成新输入的结论。现在改为按维度缓存 + 手动刷新。

运行：cd server && python -m unittest tests.test_insights_cache -v
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai
import db
from routers import orders
from routers import tax as tax_router
from schemas import InsightIn, VatIn


class TestInsightsCache(unittest.TestCase):
    """经营洞察按月缓存"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "insights.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        # 统计 AI 真实调用次数，用它判断是否命中缓存
        self.calls = []
        self._orig = ai.generate_insights

        def fake(monthly, prev_context="", business_days=None):
            self.calls.append(monthly.get("period"))
            return f"[FAKE-{monthly.get('period')}] 本月洞察内容"

        ai.generate_insights = fake
        self.addCleanup(setattr, ai, "generate_insights", self._orig)
        with db.get_conn() as conn:
            conn.execute("DELETE FROM domain_context")

    def _with_key(self):
        return mock.patch.object(ai, "ai_available", return_value=True)

    def test_first_call_generates(self):
        with self._with_key():
            r = orders.order_insights(InsightIn(year=2026, month=9))
        self.assertFalse(r["cached"])
        self.assertEqual(len(self.calls), 1)
        self.assertIn("FAKE-2026-09", r["insights"])

    def test_second_call_hits_cache(self):
        """核心回归：重复请求不再调 AI（原实现每次都调）。"""
        with self._with_key():
            orders.order_insights(InsightIn(year=2026, month=9))
            r2 = orders.order_insights(InsightIn(year=2026, month=9))
        self.assertTrue(r2["cached"])
        self.assertEqual(len(self.calls), 1, "第二次请求不应再调 AI")
        self.assertIn("FAKE-2026-09", r2["insights"])
        self.assertTrue(r2.get("updated_at"))

    def test_refresh_forces_regeneration(self):
        with self._with_key():
            orders.order_insights(InsightIn(year=2026, month=9))
            r2 = orders.order_insights(InsightIn(year=2026, month=9, refresh=True))
        self.assertFalse(r2["cached"])
        self.assertEqual(len(self.calls), 2, "refresh=true 应重新调 AI")

    def test_cache_is_per_month(self):
        """缓存必须按月分键：原实现固定键会让 9 月请求返回 8 月内容。"""
        with self._with_key():
            r9 = orders.order_insights(InsightIn(year=2026, month=9))
            r8 = orders.order_insights(InsightIn(year=2026, month=8))
        self.assertIn("2026-09", r9["insights"])
        self.assertIn("2026-08", r8["insights"])
        self.assertEqual(len(self.calls), 2, "不同月份应各自生成")

    def test_legacy_fixed_key_not_reused_across_months(self):
        """旧版固定键的缓存只在期间匹配时复用，避免张冠李戴。"""
        db.set_domain_context("ledger", "monthly_insights", "2026-08 的旧内容")
        with self._with_key():
            r = orders.order_insights(InsightIn(year=2026, month=9))
        self.assertNotIn("2026-08 的旧内容", r["insights"])
        self.assertEqual(len(self.calls), 1)

    def test_legacy_key_reused_when_period_matches(self):
        db.set_domain_context("ledger", "monthly_insights", "2026-09 的旧内容")
        with self._with_key():
            r = orders.order_insights(InsightIn(year=2026, month=9))
        self.assertIn("2026-09 的旧内容", r["insights"])
        self.assertTrue(r["cached"])
        self.assertEqual(len(self.calls), 0, "命中旧缓存时不该调 AI")

    def test_no_key_uses_degraded_without_caching(self):
        """无 Key 时走降级模板：不产生真实模型调用，也不写缓存。

        注意：无 Key 分支仍会调用 ai.generate_insights()，由它内部判断
        ai_available() 后返回模板。所以判断依据应是"有没有真的请求模型"
        （ai.chat），而不是 generate_insights 的调用次数。
        """
        chat_calls = []
        with mock.patch.object(ai, "ai_available", return_value=False), \
             mock.patch.object(ai, "chat",
                               side_effect=lambda *a, **k: chat_calls.append(1) or "x"):
            r = orders.order_insights(InsightIn(year=2026, month=9))
        self.assertFalse(r["ai_used"])
        self.assertFalse(r["cached"])
        self.assertEqual(len(chat_calls), 0, "无 Key 不该产生真实模型请求")
        self.assertTrue(r["insights"].strip())
        # 降级内容不该被写成"AI 缓存"，否则以后切到有 Key 也会命中模板
        hit = db.get_domain_context("ledger", "monthly_insights:2026-09")
        self.assertIsNone(hit, "降级结果不应写入缓存")


class TestTaxAdviceCache(unittest.TestCase):
    """报税建议按销售额分桶缓存"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "taxadvice.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.calls = []
        self._orig = ai.generate_tax_advice

        def fake(revenue, vat_result, prev_advice=""):
            self.calls.append(revenue)
            return f"[ADVICE-{revenue:.0f}] 建议内容"

        ai.generate_tax_advice = fake
        self.addCleanup(setattr, ai, "generate_tax_advice", self._orig)
        with db.get_conn() as conn:
            conn.execute("DELETE FROM domain_context WHERE domain='tax'")

    def _with_key(self):
        return mock.patch.object(ai, "ai_available", return_value=True)

    def test_first_call_generates(self):
        with self._with_key():
            r = tax_router.tax_advice(VatIn(quarterly_revenue=350000))
        self.assertFalse(r["cached"])
        self.assertIn("ADVICE-350000", r["advice"])
        self.assertEqual(r["vat_result"]["vat"], 10194.17)

    def test_same_bucket_hits_cache(self):
        """核心回归：同档销售额重复请求不再调 AI。"""
        with self._with_key():
            tax_router.tax_advice(VatIn(quarterly_revenue=350000))
            r2 = tax_router.tax_advice(VatIn(quarterly_revenue=350120))
        self.assertTrue(r2["cached"])
        self.assertEqual(len(self.calls), 1, "同档（1000 元）应命中缓存")

    def test_different_bucket_regenerates(self):
        """不同档位必须各自生成，不能把上一条建议混用。"""
        with self._with_key():
            tax_router.tax_advice(VatIn(quarterly_revenue=350000))
            r2 = tax_router.tax_advice(VatIn(quarterly_revenue=400000))
        self.assertFalse(r2["cached"])
        self.assertIn("ADVICE-400000", r2["advice"])
        self.assertEqual(len(self.calls), 2)

    def test_exemption_boundary_kept_separate(self):
        """免征线两侧必须分开缓存（增值税额不同，建议内容就不同）。"""
        with self._with_key():
            below = tax_router.tax_advice(VatIn(quarterly_revenue=300000))
            above = tax_router.tax_advice(VatIn(quarterly_revenue=301000))
        self.assertTrue(below["vat_result"]["exempt"])
        self.assertFalse(above["vat_result"]["exempt"])
        self.assertFalse(above["cached"], "跨免征线不该命中同一条建议")

    def test_refresh_forces_regeneration(self):
        with self._with_key():
            tax_router.tax_advice(VatIn(quarterly_revenue=350000))
            r2 = tax_router.tax_advice(VatIn(quarterly_revenue=350000, refresh=True))
        self.assertFalse(r2["cached"])
        self.assertEqual(len(self.calls), 2)

    def test_no_key_degrades_without_caching(self):
        with mock.patch.object(ai, "ai_available", return_value=False):
            r = tax_router.tax_advice(VatIn(quarterly_revenue=350000))
        self.assertFalse(r["ai_used"])
        self.assertFalse(r["cached"])
        self.assertTrue(r["advice"].strip())
        hit = db.get_domain_context("tax", "quarterly_advice:350000")
        self.assertIsNone(hit, "降级建议不应写入缓存")


if __name__ == "__main__":
    unittest.main(verbosity=2)
