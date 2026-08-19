# -*- coding: utf-8 -*-
"""发票台账测试：销项/进项发票、作废、汇总

运行：cd server && python -m unittest tests.test_invoice -v
"""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db


class TestInvoice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "test.db"
        db.init_db()
        cls.month = date.today().strftime("%Y-%m")
        cls.out_id = db.add_invoice("out", "客户公司", "FZ20260001", 5000, 0.03,
                                    145.63, date.today().isoformat(), "开给客户")
        cls.in_id = db.add_invoice("in", "供应商", "IN20260001", 2000, 0.03,
                                   58.25, date.today().isoformat(), "进项票")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_list_by_kind(self):
        outs = db.list_invoices(kind="out")
        self.assertTrue(all(i["kind"] == "out" for i in outs))
        self.assertTrue(any(i["id"] == self.out_id for i in outs))

    def test_summary(self):
        r = db.invoice_summary(self.month)
        out = [b for b in r["by_kind"] if b["kind"] == "out"][0]
        self.assertEqual(out["total"], 5000)
        self.assertIn("销项开了 1 张", r["summary"])

    def test_void(self):
        self.assertTrue(db.void_invoice(self.out_id))
        # 作废后 summary 不含它
        r = db.invoice_summary(self.month)
        out = [b for b in r["by_kind"] if b["kind"] == "out"][0]
        self.assertEqual(out["cnt"], 0)

    def test_update_invoice(self):
        self.assertTrue(db.update_invoice(self.in_id, amount=2500))
        lst = db.list_invoices(kind="in")
        row = [i for i in lst if i["id"] == self.in_id][0]
        self.assertEqual(row["amount"], 2500)


if __name__ == "__main__":
    unittest.main(verbosity=2)