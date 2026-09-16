# -*- coding: utf-8 -*-
"""交易更正测试：编辑 / 作废 / 退货冲销（含审计与合计口径）。

为什么这块要测得细：财务数据一旦改错比不改更糟。重点验证
  - 更正后**合计口径正确**（作废的要消失、退货的要抵消）
  - 凭证始终**借贷平衡**，且冲销方向对调
  - 每一步都**留痕**（不能悄悄消失）
  - 非法输入被拒绝（负数金额、错分类、重复作废）

运行：cd server && python -m unittest tests.test_corrections -v
"""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db

YEAR, MONTH = date.today().year, date.today().month


class TestCorrections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "corr.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        with db.get_conn() as conn:
            for t in ("transactions", "vouchers", "voucher_entries",
                      "transaction_audits", "customers"):
                conn.execute(f"DELETE FROM {t}")

    # ---------- 辅助 ----------

    def _income(self, amount=100, item="卖早餐", category="主营业务收入"):
        tid, _ = db.add_transaction(None, item, amount, "income", category)
        return tid

    def _expense(self, amount=50, item="进货", category="进货"):
        tid, _ = db.add_transaction(None, item, amount, "expense", category)
        return tid

    def _entries(self, txn_id):
        """该交易所有凭证分录（含冲销追加的）。"""
        with db.get_conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT e.*, v.voucher_no, v.status AS vstatus FROM voucher_entries e "
                "JOIN vouchers v ON v.id=e.voucher_id "
                "WHERE v.transaction_id=? ORDER BY e.id", (txn_id,)).fetchall()]

    def _balanced(self, entries):
        """借贷是否平衡（借方合计 == 贷方合计）。"""
        d = sum(e["amount"] for e in entries if e["direction"] == "debit")
        c = sum(e["amount"] for e in entries if e["direction"] == "credit")
        return abs(d - c) < 0.01

    def _monthly(self):
        return db.monthly_summary(YEAR, MONTH)

    # ---------- 编辑 ----------

    def test_edit_updates_fields_and_keeps_balance(self):
        tid = self._income(100)
        r = db.edit_transaction(tid, amount=120, item="卖早餐（含豆浆）",
                                reason="金额记错")
        self.assertTrue(r["ok"])
        self.assertEqual(r["transaction"]["amount"], 120)
        self.assertEqual(r["transaction"]["item"], "卖早餐（含豆浆）")
        self.assertTrue(self._balanced(self._entries(tid)), "更正后凭证仍须借贷平衡")

    def test_edit_old_voucher_voided_new_one_created(self):
        """旧凭证作废、新凭证生成 —— 不是就地改写，便于查账。"""
        tid = self._income(100)
        before = self._entries(tid)
        self.assertEqual(len(before), 2)
        db.edit_transaction(tid, amount=200, reason="改")
        after = self._entries(tid)
        self.assertGreater(len(after), 2, "应追加新凭证分录")
        with db.get_conn() as conn:
            statuses = [r[0] for r in conn.execute(
                "SELECT DISTINCT status FROM vouchers WHERE transaction_id=?",
                (tid,)).fetchall()]
        self.assertIn("void", statuses, "旧凭证应被作废")
        self.assertIn("approved", statuses, "应有新凭证")

    def test_edit_rejects_invalid_category(self):
        """分类无效会被凭证兜底到错科目，必须在入口拒绝。"""
        tid = self._income(100)
        with self.assertRaises(ValueError) as cm:
            db.edit_transaction(tid, category="不存在的分类")
        self.assertIn("分类无效", str(cm.exception))

    def test_edit_rejects_negative_amount(self):
        tid = self._income(100)
        with self.assertRaises(ValueError):
            db.edit_transaction(tid, amount=-5)

    def test_edit_rejects_unknown_field(self):
        tid = self._income(100)
        with self.assertRaises(ValueError) as cm:
            db.edit_transaction(tid, created_at="hack")
        self.assertIn("不支持修改", str(cm.exception))

    def test_edit_changes_monthly_total(self):
        self._income(100)
        self.assertEqual(self._monthly()["income"], 100)
        tid = db.list_transactions(YEAR, MONTH)[0]["id"]
        db.edit_transaction(tid, amount=250)
        self.assertEqual(self._monthly()["income"], 250, "合计应反映更正后的金额")

    # ---------- 作废 ----------

    def test_void_removes_from_totals_but_keeps_record(self):
        tid = self._income(100)
        self._income(50)
        self.assertEqual(self._monthly()["income"], 150)
        db.void_transaction(tid, reason="重复记账")
        self.assertEqual(self._monthly()["income"], 50, "作废的不该计入合计")
        # 记录仍在（可查），且带原因
        txn = db.get_transaction(tid)
        self.assertIsNotNone(txn, "作废不能等于删除")
        self.assertEqual(txn["status"], "voided")
        self.assertEqual(txn["voided_reason"], "重复记账")

    def test_void_reverses_voucher_direction(self):
        tid = self._income(100)
        db.void_transaction(tid, reason="测试")
        entries = self._entries(tid)
        # 原分录：借库存现金/贷主营业务收入；冲销应出现反向
        debits = [e for e in entries if e["direction"] == "debit"]
        credits = [e for e in entries if e["direction"] == "credit"]
        self.assertEqual(len(debits), 2)
        self.assertEqual(len(credits), 2)
        self.assertTrue(self._balanced(entries))

    def test_void_excluded_from_transaction_list(self):
        tid = self._income(100)
        self._income(60)
        db.void_transaction(tid)
        rows = db.list_transactions(YEAR, MONTH)
        self.assertEqual(len(rows), 1, "作废的应从流水列表消失")
        self.assertEqual(rows[0]["amount"], 60)

    def test_void_excluded_from_store_stats(self):
        """单店模型反推收入也不能把作废的算进去。"""
        self._income(1000)
        tid = db.list_transactions(YEAR, MONTH)[0]["id"]
        self._expense(400)
        before = db.store_ledger_stats(YEAR, MONTH)
        db.void_transaction(tid)
        after = db.store_ledger_stats(YEAR, MONTH)
        self.assertLess(after["income_total"], before["income_total"])

    def test_void_twice_rejected(self):
        tid = self._income(100)
        db.void_transaction(tid)
        with self.assertRaises(ValueError) as cm:
            db.void_transaction(tid)
        self.assertIn("不能再次", str(cm.exception))

    def test_void_missing_transaction(self):
        with self.assertRaises(ValueError):
            db.void_transaction(999999)

    # ---------- 退货冲销 ----------

    def test_full_refund_offsets_income(self):
        tid = self._income(100)
        self._income(50)
        r = db.refund_transaction(tid, reason="顾客退货")
        self.assertEqual(r["amount"], -100)
        self.assertTrue(r["fully_refunded"])
        self.assertEqual(self._monthly()["income"], 50, "全额退货后只剩另一笔")

    def test_partial_refund_keeps_original_active(self):
        tid = self._income(100)
        r = db.refund_transaction(tid, amount=30, reason="部分退")
        self.assertFalse(r["fully_refunded"])
        self.assertEqual(self._monthly()["income"], 70)
        self.assertEqual(db.get_transaction(tid)["status"], "active",
                         "部分退货后原交易仍有效")

    def test_refund_creates_linked_negative_record(self):
        tid = self._income(100)
        r = db.refund_transaction(tid)
        refund = db.get_transaction(r["refund_id"])
        self.assertEqual(refund["parent_id"], tid, "退货记录应关联原交易")
        self.assertEqual(refund["status"], "refund")
        self.assertLess(refund["amount"], 0, "退货金额必须为负才能自动抵消")
        self.assertIn("退货", refund["note"])

    def test_refund_voucher_reverses_direction(self):
        tid = self._income(100)
        r = db.refund_transaction(tid)
        entries = self._entries(r["refund_id"])
        self.assertEqual(len(entries), 2)
        self.assertTrue(self._balanced(entries))
        # 冲销方向对调：借主营业务收入 / 贷库存现金
        debit = next(e for e in entries if e["direction"] == "debit")
        self.assertIn("收入", debit["account_name"])

    def test_refund_rejects_amount_over_original(self):
        tid = self._income(100)
        with self.assertRaises(ValueError) as cm:
            db.refund_transaction(tid, amount=200)
        self.assertIn("超过", str(cm.exception))

    def test_refund_rejects_expense(self):
        """支出类的"退"语义不同，应引导用编辑/作废，避免账目混乱。"""
        tid = self._expense(50)
        with self.assertRaises(ValueError) as cm:
            db.refund_transaction(tid)
        self.assertIn("收入类", str(cm.exception))

    def test_refund_rejects_zero_amount(self):
        tid = self._income(100)
        with self.assertRaises(ValueError):
            db.refund_transaction(tid, amount=0)

    # ---------- 审计 ----------

    def test_every_correction_is_audited(self):
        tid = self._income(100)
        db.edit_transaction(tid, amount=120, reason="改金额")
        db.refund_transaction(tid, amount=20, reason="退一点")
        db.void_transaction(tid, reason="最后作废")
        audits = db.list_transaction_audits(tid)
        actions = [a["action"] for a in audits]
        self.assertEqual(set(actions), {"edit", "refund", "void"})
        for a in audits:
            self.assertTrue(a["created_at"])

    def test_audit_records_before_and_after(self):
        tid = self._income(100)
        db.edit_transaction(tid, amount=250, reason="翻倍")
        a = db.list_transaction_audits(tid)[0]
        self.assertIn("100", a["before_json"])
        self.assertIn("250", a["after_json"])
        self.assertEqual(a["reason"], "翻倍")

    def test_audit_list_global(self):
        self._income(10)
        self._income(20)
        db.void_transaction(db.list_transactions(YEAR, MONTH)[0]["id"], reason="x")
        self.assertGreaterEqual(len(db.list_transaction_audits()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
