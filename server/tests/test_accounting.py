# -*- coding: utf-8 -*-
"""会计闭环测试：科目余额表 / 利润表 / 资产负债表 / 期末结转。

财务模块的测试重点不是"跑得通"，而是**会计不变量必须成立**：
  - 借贷平衡（所有科目净额之和 = 0）
  - 资产负债表恒等式（资产 = 负债 + 所有者权益）
  - 利润表净利与"收入−费用"一致
  - 结转幂等（重复结转不会把利润算两遍）
  - 更正流水后重算结转结果正确

运行：cd server && python -m unittest tests.test_accounting -v
"""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import accounting
import db

PERIOD = f"{date.today().year:04d}-{date.today().month:02d}"


class TestAccounting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "acct.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        with db.get_conn() as conn:
            for t in ("voucher_entries", "vouchers", "transactions",
                      "opening_balances", "period_closings"):
                conn.execute(f"DELETE FROM {t}")

    def _seed(self):
        """造一套小账：收入 100、进货 60。"""
        db.add_transaction(None, "卖早餐", 100, "income", "主营业务收入")
        db.add_transaction(None, "进面粉", 60, "expense", "进货")

    # ---------- 科目余额表 ----------

    def test_trial_balance_is_balanced(self):
        """借贷必须平衡 —— 不平说明凭证有问题，报表不可信。"""
        self._seed()
        tb = accounting.trial_balance(PERIOD)
        self.assertTrue(tb["balanced"], f"借贷不平衡：{tb['net_sum']}")
        self.assertAlmostEqual(tb["total_debit"], tb["total_credit"], places=2)
        self.assertAlmostEqual(tb["net_sum"], 0, places=2)

    def test_one_sided_opening_balance_does_not_fake_imbalance(self):
        """只录了期初资产（没录来源）时，余额表不能报"借贷不平衡"。

        期初余额是按科目逐个录入的：店主把"开业时银行里有 2 万"录进去，
        会计上对应的是业主投入（权益），但凭证里没有这笔。资产负债表会显式
        补一行「实收资本（期初投入）」所以是平的；余额表早期直接要求
        net_sum == 0，于是同一份数据出现"资产负债表平衡、余额表不平衡"的
        自相矛盾。现在两种口径分开：balanced 只管凭证，期初差额用 opening_gap 说明。
        """
        self._seed()
        accounting.set_opening_balance("100201", 20000, "开业存入银行")
        tb = accounting.trial_balance(PERIOD)
        self.assertTrue(tb["balanced"],
                        f"凭证本身是平的，不该报不平衡：net_sum={tb['net_sum']}")
        self.assertAlmostEqual(tb["net_sum"], 0, places=2)
        self.assertAlmostEqual(tb["opening_gap"], 20000, places=2)
        self.assertIn("期初", tb["note"])
        # 同一份数据下资产负债表也必须平衡 —— 两张表不能互相打架
        bs = accounting.balance_sheet()
        self.assertTrue(bs["balanced"],
                        f"资产 {bs['total_assets']} vs 负债+权益 "
                        f"{bs['liabilities_and_equity']}")

    def test_opening_balance_without_opening_flag_is_ignored(self):
        """opening=False 时不该把期初算进期末净额（结转流程依赖这一点）。"""
        self._seed()
        accounting.set_opening_balance("100201", 20000)
        tb = accounting.trial_balance(PERIOD, opening=False)
        by = {x["account_code"]: x for x in tb["lines"]}
        self.assertAlmostEqual(by["100201"]["closing"], -60, places=2)
        self.assertAlmostEqual(tb["opening_gap"], 0, places=2)

    def test_trial_balance_lines(self):
        self._seed()
        tb = accounting.trial_balance(PERIOD)
        by = {x["account_code"]: x for x in tb["lines"]}
        # 收入 100：借库存现金 100 / 贷主营业务收入 100
        self.assertAlmostEqual(by["1001"]["debit"], 100, places=2)
        self.assertAlmostEqual(by["5001"]["closing"], -100, places=2)  # 收入类贷增
        # 进货 60：借主营业务成本 60 / 贷银行存款 60
        self.assertAlmostEqual(by["5401"]["closing"], 60, places=2)
        self.assertAlmostEqual(by["100201"]["closing"], -60, places=2)

    def test_income_statement_shows_only_period_activity(self):
        """利润表只能反映**本期发生额**，不能把期初累计当成本期利润。

        真实缺陷：早期实现直接取余额表的期末余额（= 期初 + 本期发生），
        而期初包含该期间之前的全部损益。于是选一个完全没有流水的期间，
        利润表也会报出利润 —— 实测"2099-01"（空期间）报出净利 700 元。
        做演示时随便点一下月份就会看到不该有的数字。
        """
        self._seed()                       # 收入 100 / 进货 60，都记在 PERIOD
        this_month = accounting.income_statement(PERIOD)
        self.assertAlmostEqual(this_month["net_profit"], 40, places=2)

        empty = accounting.income_statement("2099-01")
        self.assertAlmostEqual(empty["total_revenue"], 0, places=2,
                               msg="空期间不该有收入（期初累计不算本期）")
        self.assertAlmostEqual(empty["total_expense"], 0, places=2,
                               msg="空期间不该有成本费用")
        self.assertAlmostEqual(empty["net_profit"], 0, places=2)

        # 不带期间 = 全部：应与唯一有流水的期间一致
        all_time = accounting.income_statement(None)
        self.assertAlmostEqual(all_time["net_profit"], 40, places=2)

    def test_income_statement_is_zero_after_closing(self):
        """结转后本期利润表要显示 0（结转凭证把损益清零了）。

        和上一条是一对：只看本期发生额，但**要**包含结转凭证。
        早期修 bug 时把结转凭证也排除掉，结果结转后仍显示原利润，
        与"已结转"自相矛盾。
        """
        self._seed()
        accounting.close_period(PERIOD)
        after = accounting.income_statement(PERIOD)
        self.assertAlmostEqual(after["net_profit"], 0, places=2,
                               msg="已结转的期间，利润表应显示 0")
        # 反结转后利润回来
        accounting.reopen_period(PERIOD)
        back = accounting.income_statement(PERIOD)
        self.assertAlmostEqual(back["net_profit"], 40, places=2)

    def test_trial_balance_period_isolation(self):
        """指定期间时期初应等于该期间之前的累计（按正常余额方向表示）。"""
        self._seed()
        tb = accounting.trial_balance(PERIOD)
        by = {x["account_code"]: x for x in tb["lines"]}
        # 本月只有本月流水，期初应为 0
        self.assertAlmostEqual(by["5001"]["opening"], 0, places=2)
        # 换一个没有流水的期间：期初应包含之前的累计。
        # 期初按"正常余额方向"表示：收入类贷增，所以期初是 +100 而不是 -100。
        other = "2099-01"
        tb2 = accounting.trial_balance(other)
        by2 = {x["account_code"]: x for x in tb2["lines"]}
        self.assertAlmostEqual(by2["5001"]["opening"], 100, places=2,
                               msg="期初应含之前期间的累计（正常余额方向）")
        self.assertAlmostEqual(by2["5001"]["debit"], 0, places=2, msg="该期间无发生额")

    def test_period_does_not_reclose_previous_period_pl(self):
        """跨期间隔离：上月已结转的损益，不能被本月再结转一遍。

        这是结转最容易错的地方 —— 若本月把上月的损益也纳入，
        利润会被重复计入。
        """
        # 造一笔"上月"的流水
        txn_id, _ = db.add_transaction(None, "上月卖货", 500, "income", "主营业务收入")
        with db.get_conn() as conn:
            conn.execute("UPDATE transactions SET created_at='2020-01-15 08:00:00' "
                         "WHERE id=?", (txn_id,))
            conn.execute("UPDATE vouchers SET voucher_date='2020-01-15' "
                         "WHERE transaction_id=?", (txn_id,))
        accounting.close_period("2020-01")          # 结转上月
        # 本月只有一笔新流水
        self._seed()
        r = accounting.close_period(PERIOD)
        self.assertAlmostEqual(r["net_profit"], 40, places=2,
                               msg="本月只应结转本月损益（40），不含上月的 500")

    # ---------- 利润表 ----------

    def test_income_statement(self):
        self._seed()
        r = accounting.income_statement(PERIOD)
        self.assertAlmostEqual(r["total_revenue"], 100, places=2)
        self.assertAlmostEqual(r["total_expense"], 60, places=2)
        self.assertAlmostEqual(r["net_profit"], 40, places=2)
        self.assertIn("主营业务收入", r["revenue"])
        self.assertIn("主营业务成本", r["expense"])

    def test_expense_shown_as_positive(self):
        """费用在余额表里是借方（正数），利润表也要显示成正数便于阅读。"""
        self._seed()
        r = accounting.income_statement(PERIOD)
        for label, amt in r["expense"].items():
            self.assertGreater(amt, 0, f"{label} 应显示为正数")

    # ---------- 资产负债表 ----------

    def test_balance_sheet_identity_holds(self):
        """资产 = 负债 + 所有者权益（含未结转损益）。"""
        self._seed()
        bs = accounting.balance_sheet()
        self.assertTrue(bs["balanced"],
                        f"会计等式不成立：资产 {bs['total_assets']} != "
                        f"负债+权益 {bs['liabilities_and_equity']}")
        self.assertAlmostEqual(bs["total_assets"],
                               bs["liabilities_and_equity"], places=2)

    def test_balance_sheet_with_opening_balance(self):
        """录了期初现金后等式仍成立。"""
        accounting.set_opening_balance("1001", 10000, "接手时的现金")
        self._seed()
        bs = accounting.balance_sheet()
        self.assertTrue(bs["balanced"])
        # 现金 10000 + 100(收) ；银行存款 -60 付货款
        self.assertAlmostEqual(bs["total_assets"], 10040, places=2)
        self.assertAlmostEqual(bs["unclosed_profit"], 40, places=2)

    def test_opening_balance_validation(self):
        with self.assertRaises(ValueError):
            accounting.set_opening_balance("9999", 100)

    # ---------- 期末结转 ----------

    def test_close_period_moves_pl_to_profit(self):
        self._seed()
        r = accounting.close_period(PERIOD)
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(r["net_profit"], 40, places=2)
        self.assertFalse(r["reclosed"])

        # 结转后损益类科目余额归零（被结转凭证冲平），本年利润为贷方 40
        tb = accounting.trial_balance(PERIOD)
        by = {x["account_code"]: x for x in tb["lines"]}
        self.assertAlmostEqual(by["5001"]["closing"], 0, places=2, msg="收入应已结转")
        self.assertAlmostEqual(by["5401"]["closing"], 0, places=2, msg="费用应已结转")
        self.assertAlmostEqual(by["3103"]["closing"], -40, places=2,
                               msg="本年利润应为贷方 40")
        self.assertTrue(tb["balanced"], "结转后仍须借贷平衡")

    def test_close_is_idempotent_with_reversal(self):
        """重复结转不能把利润算两遍：应先红冲旧结转凭证再重算。"""
        self._seed()
        accounting.close_period(PERIOD)
        r2 = accounting.close_period(PERIOD)
        self.assertTrue(r2["reclosed"], "第二次应标记为重新结转")

        tb = accounting.trial_balance(PERIOD)
        by = {x["account_code"]: x for x in tb["lines"]}
        self.assertAlmostEqual(by["3103"]["closing"], -40, places=2,
                               msg="重新结转后本年利润仍应只有 40")
        self.assertTrue(tb["balanced"])

    def test_reclose_after_correction(self):
        """更正流水后重算结转，结果要跟着变（这是重算能力的意义）。"""
        self._seed()
        accounting.close_period(PERIOD)
        # 把那笔进货改小（60 → 20），利润应从 40 变 80
        rows = db.list_transactions()
        txn = next(t for t in rows if t["trans_type"] == "expense")
        db.edit_transaction(txn["id"], amount=20, reason="金额记错")
        r = accounting.close_period(PERIOD)
        self.assertAlmostEqual(r["net_profit"], 80, places=2)
        tb = accounting.trial_balance(PERIOD)
        by = {x["account_code"]: x for x in tb["lines"]}
        self.assertAlmostEqual(by["3103"]["closing"], -80, places=2)
        self.assertTrue(tb["balanced"])

    def test_void_after_close_recomputes(self):
        """作废流水后重算结转：利润应减少。"""
        self._seed()
        rows = db.list_transactions()
        income = next(t for t in rows if t["trans_type"] == "income")
        db.void_transaction(income["id"], reason="这笔没收")
        r = accounting.close_period(PERIOD)
        self.assertAlmostEqual(r["net_profit"], -60, places=2,
                               msg="收入没了，只剩成本 → 亏损 60")
        self.assertTrue(accounting.trial_balance(PERIOD)["balanced"])

    def test_reopen_period(self):
        self._seed()
        accounting.close_period(PERIOD)
        r = accounting.reopen_period(PERIOD, reason="账目要重做")
        self.assertTrue(r["ok"])
        self.assertEqual(r["closing"]["status"], "reopened")
        # 反结转后损益类余额回来
        tb = accounting.trial_balance(PERIOD)
        by = {x["account_code"]: x for x in tb["lines"]}
        self.assertAlmostEqual(by["5001"]["closing"], -100, places=2)
        self.assertTrue(tb["balanced"])

    def test_reopen_unknown_period(self):
        with self.assertRaises(ValueError):
            accounting.reopen_period("2099-01")

    def test_close_rejects_bad_period(self):
        for bad in ("", "2026", "2026/01", "202601"):
            with self.assertRaises(ValueError):
                accounting.close_period(bad)

    def test_list_closings(self):
        self._seed()
        accounting.close_period(PERIOD)
        rows = accounting.list_closings()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["period"], PERIOD)
        self.assertTrue(rows[0]["voucher_no"])

    def test_close_without_activity(self):
        """没有任何流水时结转不应崩，净利为 0。"""
        r = accounting.close_period(PERIOD)
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(r["net_profit"], 0, places=2)
        self.assertTrue(accounting.trial_balance(PERIOD)["balanced"])

    def test_close_creates_balanced_voucher(self):
        """结转凭证本身必须借贷平衡（金额相等、方向对调）。"""
        self._seed()
        r = accounting.close_period(PERIOD)
        vid = r["closing"]["voucher_id"]
        with db.get_conn() as conn:
            entries = [dict(x) for x in conn.execute(
                "SELECT * FROM voucher_entries WHERE voucher_id=?", (vid,)).fetchall()]
        debits = sum(e["amount"] for e in entries if e["direction"] == "debit")
        credits = sum(e["amount"] for e in entries if e["direction"] == "credit")
        self.assertAlmostEqual(debits, credits, places=2)
        # 所有金额都应是正的数量级（方向由借贷表示）
        self.assertTrue(all(e["amount"] > 0 for e in entries),
                        "凭证金额不应为负")


if __name__ == "__main__":
    unittest.main(verbosity=2)
