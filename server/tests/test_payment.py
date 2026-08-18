# -*- coding: utf-8 -*-
"""收款流水同步测试：DEMO 模式 / 幂等去重 / 日志 / 一键清空 / CSV 解析（无需启动服务器）

运行：cd server && python -m unittest tests.test_payment -v
"""
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import payment
import wechat_pay


class TestPaymentSync(unittest.TestCase):
    """DEMO 模式全链路：建账户 → 同步 → 入账本 → 幂等 → 清空"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "test.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        # 每个用例独立账户，避免相互污染
        self.sid = db.save_payment_source(
            source_type="wechat", name="演示收款码",
            mchid="DEMO", enabled=1)
        self.bill_date = (date.today() - timedelta(days=1)).isoformat()

    def test_demo_sync_imports_txns(self):
        """DEMO 同步：拉取 → 入账本，交易数>0、金额>0、来源标记 wechat"""
        r = payment.run_sync(self.sid, self.bill_date)
        self.assertTrue(r["ok"], r)
        self.assertGreater(r["fetched"], 0)
        self.assertEqual(r["imported"], r["fetched"])
        txns = db.list_transactions(
            year=int(self.bill_date[:4]), month=int(self.bill_date[5:7]), limit=200)
        demo = [t for t in txns if t["source"] == "wechat"]
        self.assertGreaterEqual(len(demo), r["imported"])
        self.assertTrue(all(t["amount"] > 0 for t in demo))
        self.assertTrue(all(t["wx_trade_id"].startswith("DEMO-") for t in demo))

    def test_sync_idempotent(self):
        """幂等：同一天重复同步不产生重复流水（wx_trade_id 唯一索引去重）"""
        payment.demo_clear()                     # 清空其它用例产生的同日期数据，保证起点一致
        r1 = payment.run_sync(self.sid, self.bill_date)
        self.assertGreater(r1["imported"], 0)
        r2 = payment.run_sync(self.sid, self.bill_date)
        self.assertEqual(r2["imported"], 0)      # 重复同步不再插入
        self.assertEqual(r2["skipped"], r1["imported"])  # 全部被唯一索引拦截
        # 同步日志记录本次两次同步
        logs = db.list_sync_logs(10)
        recent = [l for l in logs if l["source_id"] == self.sid and l["bill_date"] == self.bill_date]
        self.assertEqual(len(recent), 2)

    def test_sync_log_fields(self):
        logs = db.list_sync_logs(10)
        log_row = logs[0]
        self.assertEqual(log_row["status"], "success")
        self.assertEqual(log_row["bill_date"], self.bill_date)
        self.assertEqual(log_row["source_name"], "演示收款码")

    def test_daily_sync_skips_done(self):
        """run_daily_sync：已同步过的日期跳过，不重复拉取（结果中不含本次 DEMO 账户）"""
        payment.run_daily_sync()
        second = payment.run_daily_sync()
        sids = [r["source_id"] for r in second]
        self.assertNotIn(self.sid, sids)

    def test_demo_clear(self):
        """一键清空 DEMO 流水"""
        deleted = payment.demo_clear()
        self.assertGreaterEqual(deleted, 0)
        txns = db.list_transactions(limit=200)
        self.assertFalse(any(t["wx_trade_id"].startswith("DEMO-") for t in txns))

    def test_aggregate_not_ready(self):
        """聚合支付未接入：同步时报错且写 error 日志（不静默失败）"""
        aid = db.save_payment_source(
            source_type="aggregate", name="聚合码", mchid="AGG001", enabled=1)
        r = payment.run_sync(aid, self.bill_date)
        self.assertFalse(r["ok"])
        self.assertIn("聚合支付通道尚未接入", r["error"])


class TestBillCsv(unittest.TestCase):
    """微信交易账单 CSV 解析：表头动态映射 + 反引号字段 + 状态过滤"""

    CSV = """微信支付账单明细,,,,,,,
,,,,,,,,
交易时间,商户订单号,微信订单号,交易类型,交易状态,商品名称,订单金额,商户号
2026-06-17 20:12:33,PA20260617001,`4200001234`,扫码支付,SUCCESS,`豆浆,油条`,12.00,1900000001
2026-06-17 21:00:00,PA20260617002,`4200005678`,扫码支付,REFUND,`退款单`,8.00,1900000001
2026-06-17 22:00:00,PA20260617003,`4200009999`,扫码支付,SUCCESS,肉包,3.50,1900000001
总单数 2,退款单数 1,,
"""

    def test_parse_skips_meta_and_failed(self):
        rows = wechat_pay._parse_csv_text(self.CSV)
        # 表头后 3 行数据（总/空行被过滤），其中 2 笔 SUCCESS
        self.assertEqual(len(rows), 3)
        txns = [t for t in (wechat_pay._row_to_txn(r) for r in rows) if t]
        self.assertEqual(len(txns), 2)
        amounts = sorted(t["amount"] for t in txns)
        self.assertEqual(amounts, [3.5, 12.0])   # REFUND 行被过滤
        self.assertTrue(all(t["wx_trade_id"] for t in txns))
        # 反引号包裹的字段被还原（去掉前后引号，且字段内逗号不影响列对齐）
        self.assertEqual(txns[0]["item"], "豆浆,油条")

    def test_parse_amount_with_commas(self):
        """字段内逗号（反引号包裹）不破坏列对齐"""
        csv2 = self.CSV.replace("`豆浆,油条`", "`奶茶,柠檬水`")
        rows = wechat_pay._parse_csv_text(csv2)
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)