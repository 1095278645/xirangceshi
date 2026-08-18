# -*- coding: utf-8 -*-
"""单店经营模型测试（勇哥方法论泛化：保本线先行）

运行：cd server && python -m unittest tests.test_store -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store as storelib


class TestPresets(unittest.TestCase):
    def test_preset_keys(self):
        for k in ("餐饮", "饮品", "零售", "生鲜", "服务", "摆摊"):
            self.assertIn(k, storelib.BUSINESS_PRESETS)

    def test_preset_has_margin_range(self):
        for k, v in storelib.BUSINESS_PRESETS.items():
            lo, hi = v["margin_range"]
            self.assertGreater(hi, lo)
            self.assertGreater(lo, 0)

    def test_fallback_preset(self):
        p = storelib.get_preset("不存在的业态")
        self.assertTrue(p["fallback"])

    def test_known_preset_no_fallback(self):
        p = storelib.get_preset("餐饮")
        self.assertFalse(p["fallback"])


class TestBreakEven(unittest.TestCase):
    """保本线核心：固定成本 16000，毛利率 0.58 → 保本月销 27586，保本日销 919.5，目标日销 1195.4"""

    def _base(self, **kw):
        args = dict(
            rent=6000, salary=8000, utilities=2000,
            biz_type="餐饮", gross_margin=0.58,
        )
        args.update(kw)
        return storelib.calc_store_model(**args)

    def test_break_even_day(self):
        r = self._base()
        m = r["model"]
        self.assertAlmostEqual(m["fixed_month"], 16000.0, places=1)
        self.assertAlmostEqual(m["break_even_day"], 919.5, places=1)
        self.assertAlmostEqual(m["target_day"], 1195.4, places=1)

    def test_healthy_when_over_target(self):
        r = self._base(daily_revenue=1500)
        self.assertEqual(r["dimensions"]["a"]["level"], "健康")
        self.assertEqual(r["overall"]["key"], "ok")

    def test_critical_when_between_lines(self):
        r = self._base(daily_revenue=1000)
        self.assertEqual(r["dimensions"]["a"]["level"], "临界")
        self.assertEqual(r["dimensions"]["a"]["score"], 50)

    def test_danger_when_below_break_even(self):
        r = self._base(daily_revenue=500)
        self.assertEqual(r["dimensions"]["a"]["level"], "危险")
        self.assertEqual(r["dimensions"]["a"]["score"], 0)
        self.assertIn("低于保本线", r["advice"])

    def test_payback_months(self):
        # 日销1500：月利润 10100 → 20万投资回本 19.8 个月
        r = self._base(daily_revenue=1500, total_investment=200_000)
        self.assertAlmostEqual(r["model"]["payback_months"], 19.8, places=1)

    def test_payback_none_when_loss(self):
        r = self._base(daily_revenue=300, total_investment=200_000)
        self.assertIsNone(r["model"]["payback_months"])

    def test_cash_months(self):
        r = self._base(daily_revenue=1500, cash_on_hand=50_000)
        self.assertAlmostEqual(r["model"]["cash_months"], 3.1, places=1)
        self.assertTrue(any("6 个月" in f for f in r["cash_flags"]))

    def test_cash_danger_flag(self):
        r = self._base(daily_revenue=1500, cash_on_hand=20_000)
        self.assertTrue(any("危险区" in f for f in r["cash_flags"]))


class TestDimensionTraffic(unittest.TestCase):
    def test_bad_traffic_danger(self):
        r = storelib.calc_store_model(daily_revenue=1500, rent=6000, salary=8000,
                                      utilities=2000, biz_type="餐饮",
                                      traffic="差", competitor="多")
        self.assertEqual(r["dimensions"]["c"]["level"], "危险")

    def test_good_traffic_healthy(self):
        r = storelib.calc_store_model(daily_revenue=1500, rent=6000, salary=8000,
                                      utilities=2000, biz_type="餐饮",
                                      traffic="好", competitor="少")
        self.assertEqual(r["dimensions"]["c"]["level"], "健康")


class TestInputGuards(unittest.TestCase):
    def test_negative_values_are_zeroed(self):
        r = storelib.calc_store_model(daily_revenue=-100, rent=-1, biz_type="餐饮")
        self.assertEqual(r["inputs"]["daily_revenue"], 0)
        self.assertEqual(r["inputs"]["rent"], 0)

    def test_margin_capped(self):
        r = storelib.calc_store_model(daily_revenue=1000, gross_margin=0.99,
                                      rent=1000, biz_type="餐饮")
        self.assertLessEqual(r["inputs"]["gross_margin"], 0.95)

    def test_margin_fallback_to_preset(self):
        r = storelib.calc_store_model(daily_revenue=1000, gross_margin=None,
                                      rent=1000, biz_type="零售")
        self.assertAlmostEqual(r["inputs"]["gross_margin"], 0.24, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)