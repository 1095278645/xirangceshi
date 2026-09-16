# -*- coding: utf-8 -*-
"""端到端彩排：按演示动线把每个页面交互打一遍真实 HTTP，逐项核对预期。

与 tests/ 下其它测试的区别：那些测单元/模块，这个测**整条动线是否还能走通**
以及**关键数值是否漂移**。演示前跑一遍，能在打开开发者工具之前发现
"某个接口字段没了""某个税额算错了""分类又落错了科目"。

AI 调用被替换为固定桩（不消耗额度、不依赖网络），因此可随时运行。

运行：cd server && python -m unittest tests.test_demo_flow -v
"""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db

YEAR, MONTH = date.today().year, date.today().month


class TestDemoFlow(unittest.TestCase):
    """演示动线端到端（AI 打桩）"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "flow.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        # AI 打桩：不联网、不耗额度；返回结构需与真实实现一致
        self._patches = [
            mock.patch("ai.ai_available", return_value=True),
            mock.patch("ai.parse_transaction", side_effect=self._fake_parse),
            mock.patch("ai.generate_insights", return_value="[桩] 经营洞察正文"),
            mock.patch("ai.generate_tax_advice", return_value="[桩] 报税建议正文"),
            mock.patch("ai.generate_reminders",
                       return_value=[{"customer": "王阿姨", "content": "[桩] 提醒"}]),
            mock.patch("ai.generate_customer_insight", return_value="[桩] 熟客画像"),
            mock.patch("ai.generate_copy",
                       return_value=("[桩] 融合文案", {"employees": ["创意文案师", "合规审核"],
                                                  "adopted": ["创意文案师"]},
                                    ["[桩] 第一条", "[桩] 第二条", "[桩] 第三条"])),
            # team 域走的是 ai.chat，直接给 JSON 桩
            mock.patch("team_domains.ai.chat",
                       return_value='{"verdict":"桩裁决","adopted":["创意文案师"],'
                                    '"final":"[桩] 最终诊断","variants":["[桩]稿1","[桩]稿2","[桩]稿3"]}'),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        # 清空数据，保证从干净状态走一遍
        with db.get_conn() as conn:
            for t in ("transactions", "vouchers", "voucher_entries", "customers",
                      "memories", "reminders", "domain_context"):
                conn.execute(f"DELETE FROM {t}")

    @staticmethod
    def _fake_parse(text):
        """按关键词给出与真实模型一致的解析结果（覆盖演示用到的两句）。"""
        if "王阿姨" in text:
            return {"customer": "王阿姨", "item": "两个肉包一杯豆浆", "amount": 6,
                    "note": "", "tags": "", "category": "主营业务收入",
                    "trans_type": "income"}
        if "李叔" in text:
            return {"customer": "李叔", "item": "猪肉两斤", "amount": 38,
                    "note": "", "tags": "", "category": "进货", "trans_type": "expense"}
        return {"customer": "", "item": "商品销售", "amount": None, "note": "",
                "tags": "", "category": "主营业务收入", "trans_type": "income"}

    def _client(self):
        import main
        from fastapi.testclient import TestClient
        return TestClient(main.app)

    # ---------------- 第 1 站 ----------------
    def test_station1_recording(self):
        with self._client() as c:
            r = c.post("/api/orders", json={"text": "王阿姨买了两个肉包一杯豆浆，6块"})
            self.assertEqual(r.status_code, 200)
            j = r.json()
            self.assertEqual(j["parsed"]["amount"], 6)
            self.assertEqual(j["parsed"]["customer"], "王阿姨")
            self.assertTrue(j.get("voucher"), "必须返回借贷凭证")

            r2 = c.post("/api/orders", json={"text": "李叔拿了两斤猪肉，38块，支出"})
            p2 = r2.json()["parsed"]
            self.assertEqual(p2["trans_type"], "expense")
            self.assertEqual(p2["category"], "进货", "进货分类决定毛利率能否算对")

            # 无金额不崩、且标记出来
            r3 = c.post("/api/orders", json={"text": "张叔拿了个包子"})
            self.assertTrue(r3.json().get("amount_missing"))

            s = c.get("/api/orders/today").json()
            self.assertEqual(s["cnt"], 3)

    # ---------------- 第 2 站 ----------------
    def test_station2_customers(self):
        with self._client() as c:
            cid, _ = db.find_or_create_customer("王阿姨", tags="老主顾", favorite="肉包,豆浆")
            db.add_memory(cid, "孙子考上一中")
            with db.get_conn() as conn:
                db.add_transaction(cid, "肉包和豆浆", 6, "income", "主营业务收入")
            db.add_reminder(cid, "问问孙子适应不适应")

            lst = c.get("/api/customers").json()
            lst = lst.get("customers", lst) if isinstance(lst, dict) else lst
            me = next(x for x in lst if x["name"] == "王阿姨")
            for k in ("id", "name", "tags", "favorite", "order_count", "last_visit"):
                self.assertIn(k, me, f"页面直接读 {k}，缺了会渲染空白")

            d = c.get(f"/api/customers/{cid}").json()
            self.assertTrue(d.get("memories"))
            self.assertTrue(d.get("transactions"))

            r = c.post("/api/memories", json={"customer_id": cid, "content": "胃不好，豆浆要热的"})
            self.assertEqual(r.status_code, 200)

            rem = c.get("/api/reminders").json()
            rem = rem.get("reminders", rem) if isinstance(rem, dict) else rem
            self.assertTrue(rem)
            self.assertIn("customer_name", rem[0], "提醒页直接读 customer_name")
            rid = rem[0]["id"]
            c.post(f"/api/reminders/{rid}/done")
            rem2 = c.get("/api/reminders").json()
            rem2 = rem2.get("reminders", rem2) if isinstance(rem2, dict) else rem2
            self.assertEqual(next(x for x in rem2 if x["id"] == rid)["done"], 1)

    # ---------------- 第 3 站 ----------------
    def test_station3_copy(self):
        with self._client() as c:
            r = c.post("/api/copy", json={"shop_name": "巷子里的早餐铺",
                                          "scene": "今日营业", "extra": ""})
            self.assertEqual(r.status_code, 200)
            j = r.json()
            self.assertTrue(j.get("text"))
            self.assertGreaterEqual(len(j.get("variants") or []), 2,
                                    "多 agent 应出多稿")
            self.assertFalse(any("|||" in str(v) for v in (j.get("variants") or [])),
                             "不应残留分隔符")

    # ---------------- 第 4 站 ----------------
    def test_station4_ledger_and_tax(self):
        with self._client() as c:
            # 造一笔收入与一笔房租，验证分类→科目映射
            cid, _ = db.find_or_create_customer("王阿姨")
            db.add_transaction(cid, "肉包", 6, "income", "主营业务收入")
            db.add_transaction(None, "门店房租", 6000, "expense", "租赁及物业费")

            tx = c.get(f"/api/transactions?year={YEAR}&month={MONTH}").json()
            tl = tx.get("transactions", tx) if isinstance(tx, dict) else tx
            self.assertTrue(tl)
            for k in ("item", "amount", "trans_type", "friendly", "created_at"):
                self.assertIn(k, tl[0], f"流水页直接读 {k}")

            vl = c.get("/api/vouchers").json()
            vl = vl.get("vouchers", vl) if isinstance(vl, dict) else vl
            self.assertTrue(vl)
            v = vl[0]
            self.assertRegex(v["voucher_no"], r"^记-\d{6}-\d{4}$")
            es = v["entries"]
            self.assertEqual(len(es), 2, "借贷各一条")
            dirs = {e["direction"]: e["account_name"] for e in es}
            self.assertEqual(set(dirs), {"debit", "credit"})
            self.assertEqual(sum(e["amount"] for e in es), 2 * es[0]["amount"], "借贷平衡")

            # 房租必须落在租赁及物业费，而不是被兜底成办公费
            m = c.get("/api/orders/monthly").json()
            cats = {x["category"]: x["total"] for x in m["categories"]}
            self.assertIn("租赁及物业费", cats)
            self.assertEqual(cats["租赁及物业费"], 6000)

            # 科目表
            at = c.get("/api/account-titles").json()
            self.assertGreater(at["total"], 0)
            self.assertTrue(at["categories"][0]["titles"])

            # 四税
            self.assertAlmostEqual(
                c.post("/api/tax/vat", json={"quarterly_revenue": 350000}).json()["vat"],
                10194.17, places=2)
            self.assertAlmostEqual(
                c.post("/api/tax/pit", json={"salary": 15000}).json()["tax"], 790, places=2)
            # 250 万 = 100万×5% + 150万×10%
            self.assertAlmostEqual(
                c.post("/api/tax/cit", json={"annual_income": 2500000,
                                             "is_small": True}).json()["total_tax"],
                200000, places=2)

            # 报表
            r = c.get(f"/api/report/monthly?year={YEAR}&month={MONTH}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.content[:2], b"PK", "应是 xlsx（zip）")

    # ---------------- 第 5 站 ----------------
    def test_station5_store_model(self):
        with self._client() as c:
            cid, _ = db.find_or_create_customer("王阿姨")
            # 造 10 天流水，让反推有料
            for i in range(10):
                db.add_transaction(cid, "肉包和豆浆", 1200, "income", "主营业务收入")
                db.add_transaction(None, "面粉猪肉", 456, "expense", "进货")

            fl = c.get("/api/store/from-ledger").json()
            self.assertTrue(fl.get("daily_revenue"))
            self.assertGreater(fl.get("gross_margin") or 0, 0)
            self.assertTrue(fl.get("note"), "页面显示来源说明")

            ps = c.get("/api/store/presets").json()
            pl = ps.get("presets", ps) if isinstance(ps, dict) else ps
            self.assertGreaterEqual(len(pl), 6, "六业态预设")

            payload = {"daily_revenue": fl["daily_revenue"],
                       "gross_margin": fl["gross_margin"],
                       "rent": 6000, "salary": 8000, "utilities": 2000,
                       "total_investment": 180000, "cash_on_hand": 50000,
                       "traffic": "一般", "competitor": "一般", "biz_type": "餐饮"}
            j = c.post("/api/store/model", json=payload).json()
            self.assertTrue(j["advice"])
            self.assertEqual(set(j["dimensions"]), {"a", "b", "c"})
            self.assertIn(j["overall"]["level"], ("健康", "临界", "危险"))

            # 加演：日销低于保本线必须翻转成危险
            j2 = c.post("/api/store/model", json={**payload, "daily_revenue": 1}).json()
            self.assertEqual(j2["overall"]["level"], "危险")

    # ---------------- 设置页 ----------------
    def test_settings_and_payment(self):
        with self._client() as c:
            s = c.get("/api/settings").json()
            self.assertIn("ai_enabled", s)
            self.assertGreaterEqual(len(c.get("/api/providers").json()["providers"]), 5)

            sid = c.post("/api/payment/sources",
                         json={"source_type": "wechat", "name": "演示收款码",
                               "mchid": "DEMO", "enabled": 1}).json()["id"]
            sl = c.get("/api/payment/sources").json()["sources"]
            self.assertTrue(sl)
            self.assertIn(sl[0]["api_v3_key"], ("", "***"), "api_v3_key 必须脱敏")

            r = c.post(f"/api/payment/sources/{sid}/sync").json()
            self.assertTrue(r["ok"], "DEMO 模式同步应成功")
            self.assertGreater(r["imported"], 0)
            self.assertTrue(c.get("/api/payment/logs").json()["logs"])

            self.assertTrue(c.post("/api/payment/demo-clear").json()["deleted"] >= 0)
            self.assertEqual(c.get("/api/heartbeat").status_code, 200)

    # ---------------- 分类→科目 一致性（彩排发现的 bug 的守卫） ----------------
    def test_all_seeded_categories_have_accounts(self):
        """账本里出现的每个品类都必须有科目映射。

        无映射会被自动凭证静默兜底到「办公费」—— 实测 6000 元房租被记成
        管理费用-办公费，账本品类与凭证科目一起错。
        """
        from categories import CATEGORY_TO_ACCOUNTS
        # 造遍演示会出现的品类
        for cat, tt in (("主营业务收入", "income"), ("进货", "expense"),
                        ("办公费", "expense"), ("租赁及物业费", "expense")):
            db.add_transaction(None, f"测试{cat}", 100, tt, cat)
        with db.get_conn() as conn:
            cats = [r[0] for r in conn.execute(
                "SELECT DISTINCT category FROM transactions").fetchall() if r[0]]
        unmapped = [c for c in cats if c not in CATEGORY_TO_ACCOUNTS]
        self.assertEqual(unmapped, [], f"这些品类没有科目映射：{unmapped}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
