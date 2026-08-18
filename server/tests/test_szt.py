# -*- coding: utf-8 -*-
"""省账通集成测试：税法计算 / 68科目 / 交易流水 / 报表导出 / 安全护栏

运行：cd server && python -m unittest tests.test_szt -v
"""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import report as reportlib
import tax as taxcalc
from categories import ACCOUNT_TITLES, ACCOUNT_CATEGORY_NAMES


class TestVat(unittest.TestCase):
    def test_exempt_within_threshold(self):
        r = taxcalc.calc_vat(300_000)
        self.assertTrue(r["exempt"])
        self.assertEqual(r["vat"], 0)

    def test_taxable_over_threshold(self):
        r = taxcalc.calc_vat(300_001)
        self.assertFalse(r["exempt"])
        self.assertAlmostEqual(r["vat"], round(300_001 / 1.03 * 0.03, 2), places=2)


class TestSurtax(unittest.TestCase):
    def test_six_tax_relief_half(self):
        r = taxcalc.calc_surtax(1000, is_small=True)
        self.assertTrue(r["six_tax_relief"])
        # 1000 * (7%+3%+2%) * 50% = 60
        self.assertAlmostEqual(r["total"], 60.0, places=2)
        self.assertEqual(len(r["items"]), 3)

    def test_no_relief_for_general(self):
        r = taxcalc.calc_surtax(1000, is_small=False)
        self.assertFalse(r["six_tax_relief"])
        self.assertAlmostEqual(r["total"], 120.0, places=2)


class TestPit(unittest.TestCase):
    def test_exempt(self):
        r = taxcalc.calc_individual_income_tax(4000)
        self.assertEqual(r["tax"], 0)

    def test_band_calculation(self):
        # 15000-5000=10000 → 档2: 10%, 速算210 → 10000*0.10-210=790
        r = taxcalc.calc_individual_income_tax(15000)
        self.assertAlmostEqual(r["taxable"], 10000, places=2)
        self.assertAlmostEqual(r["tax"], 790, places=2)

    def test_with_social_and_special(self):
        r = taxcalc.calc_individual_income_tax(15000, social_insurance=1500, special_deduction=1000)
        self.assertAlmostEqual(r["taxable"], 7500, places=2)


class TestCit(unittest.TestCase):
    def test_small_enterprise_brackets(self):
        # 250万：100万*5% + 150万*10% = 20万
        r = taxcalc.calc_corporate_income_tax(2_500_000, is_small=True)
        self.assertEqual(len(r["details"]), 2)
        self.assertAlmostEqual(r["total_tax"], 200_000, places=2)

    def test_general_standard_rate(self):
        r = taxcalc.calc_corporate_income_tax(10_000_000, is_small=False)
        self.assertAlmostEqual(r["tax"], 2_500_000, places=2)


class TestCalendar(unittest.TestCase):
    def test_monthly_and_quarterly(self):
        r = taxcalc.get_filing_calendar(2026, 4)   # 4月为季度申报月
        types = [x["tax_type"] for x in r["reminders"]]
        self.assertIn("增值税", types)
        self.assertIn("企业所得税", types)

    def test_annual_recon_may(self):
        r = taxcalc.get_filing_calendar(2026, 5)
        self.assertTrue(any("汇算清缴" in x["note"] for x in r["reminders"]))

    def test_plain_month_two_reminders(self):
        r = taxcalc.get_filing_calendar(2026, 2)
        self.assertEqual(len(r["reminders"]), 2)


class TestAccountTitles(unittest.TestCase):
    def test_66_titles(self):
        self.assertEqual(len(ACCOUNT_TITLES), 66)

    def test_category_names(self):
        self.assertIn("asset", ACCOUNT_CATEGORY_NAMES)
        self.assertEqual(ACCOUNT_CATEGORY_NAMES["asset"], "资产类")

    def test_used_codes_exist(self):
        """凭证用到的科目必须在科目表里"""
        codes = {"1001", "100201", "2211", "5001", "5051", "5401",
                 "560101", "560102", "560103", "560104", "560105",
                 "560106", "560109", "560110", "560111", "560112"}
        table_codes = {t[0] for t in ACCOUNT_TITLES}
        self.assertTrue(codes <= table_codes)


class TestGuard(unittest.TestCase):
    def test_large_amount_warn(self):
        r = taxcalc.check_amount_guard(60_000)
        self.assertEqual(r["level"], "warn")

    def test_large_amount_high(self):
        r = taxcalc.check_amount_guard(600_000)
        self.assertEqual(r["level"], "high")

    def test_normal_amount(self):
        self.assertIsNone(taxcalc.check_amount_guard(1000))

    def test_boundary_scenario(self):
        r = taxcalc.detect_boundary("今天去工商注册新店")
        self.assertIsNotNone(r)
        self.assertEqual(r["code"], "B01")
        self.assertIsNone(taxcalc.detect_boundary("王阿姨买了两个肉包六块"))


class TestLedgerExtension(unittest.TestCase):
    """交易流水 + 报表导出（临时库）"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "test.db"
        db.init_db()
        db.add_transaction(None, "进了一批货", 120, "expense", "进货")
        cid, _ = db.find_or_create_customer("王阿姨")
        db.add_transaction(cid, "王阿姨买包子", 6, "income", "主营业务收入", counterparty="王阿姨")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_list_transactions(self):
        today = date.today()
        rows = db.list_transactions(today.year, today.month)
        self.assertGreaterEqual(len(rows), 2)
        self.assertIn("friendly", rows[0])
        self.assertNotRegex(rows[0]["friendly"], r"^\d+$")

    def test_list_transactions_filters_period(self):
        rows = db.list_transactions(1999, 1)   # 空月份
        self.assertEqual(rows, [])

    def test_monthly_report_export(self):
        out = reportlib.get_monthly_report(date.today().year, date.today().month,
                                          out_dir=str(self._tmp.name))
        self.assertEqual(out["status"], "ok")
        self.assertTrue(Path(out["file"]).exists())
        self.assertGreater(Path(out["file"]).stat().st_size, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)