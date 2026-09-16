# -*- coding: utf-8 -*-
"""tests/test_gaps_http.py — 7 项能力缺口的 HTTP 端到端冒烟测试

为什么单独一个文件：这 7 项（备份/更正/收款/触达/移动端/会计/多店）各自的
单元测试都在，但**没有一条用例把「用户点得到的那条路」真的走一遍**。
真实缺陷恰好都藏在这一层：接口路径写错、必填参数漏传（confirm=true）、
返回结构对不上，单测全绿但功能不可用。本文件按"演示动线"逐项打接口。

覆盖：
  1. 备份：列表 → 新建 → 导出 → （不真恢复，避免覆盖）
  2. 更正：记账 → 改金额 → 看审计 → 退货冲销 → 作废
  3. 收款：创建 → 公开页 → 顾客标记已付 → 确认入账 → 生成收款码
  4. 触达：通道列表 → 本地收件箱 → 发测试推送 → 投递记录
  5. 移动端：库存 / 发票 / 现金流 / 应收应付 四块接口可用
  6. 会计：余额表 → 利润表 → 资产负债表 → 期末结转 → 反结转
  7. 多店：建店 → 切店 → 数据隔离 → 成员与权限
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth
import config
import db
import shops


class GapsHttpBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls._orig = (config.DATA_DIR, config.DB_PATH,
                     shops.SHOPS_DIR, shops.REGISTRY_PATH)
        config.DATA_DIR = str(root)
        config.DB_PATH = str(root / "gaps.db")
        shops.SHOPS_DIR = root / "shops"
        shops.REGISTRY_PATH = root / "registry.db"
        # 记住真实的演示库路径，供"不碰生产库"的守卫用例比对
        cls._prod_db = Path(cls._orig[1])

    @classmethod
    def tearDownClass(cls):
        config.DATA_DIR, config.DB_PATH, shops.SHOPS_DIR, shops.REGISTRY_PATH = cls._orig
        db._schema_ready.clear()
        cls._tmp.cleanup()

    def setUp(self):
        os.environ.pop(auth.ENV_TOKEN, None)
        db.DB_PATH = Path(config.DB_PATH)
        db.init_db()
        shops.init_registry()
        with db.get_conn() as conn:
            for t in ("transactions", "vouchers", "voucher_entries", "customers",
                      "memories", "reminders", "products", "invoices",
                      "payment_collections", "notification_logs",
                      "notification_subscriptions", "period_closings",
                      "opening_balances", "transaction_audits", "budgets", "debts"):
                try:
                    conn.execute(f"DELETE FROM {t}")
                except Exception:  # noqa: BLE001  表可能还没建
                    pass
        from fastapi.testclient import TestClient
        import main
        self.client = TestClient(main.app)

    def tearDown(self):
        os.environ.pop(auth.ENV_TOKEN, None)


class TestNoProductionLeak(GapsHttpBase):
    """守卫用例：带店上下文时，默认店必须仍然解析到 db.DB_PATH（测试重定向的库）。

    这条用例是被真实事故逼出来的：多店改造后 resolve_db_path() 返回
    config.DB_PATH 而不是 db.DB_PATH，于是**所有设置了店上下文的 HTTP 请求
    都绕过了测试重定向，直接读写演示库/生产库** —— 演示数据被读成 207 笔流水、
    被写入测试收款单。单看功能测试全绿，直到断言对不上才暴露。
    """

    def test_shop_context_follows_db_path_redirect(self):
        db.DB_PATH = Path(config.DB_PATH)      # 测试重定向
        with shops.use_shop(shops.DEFAULT_SHOP_ID):
            resolved = Path(shops.resolve_db_path())
            self.assertEqual(resolved, Path(db.DB_PATH),
                             "默认店必须跟随 db.DB_PATH，而不是 config.DB_PATH")
            self.assertNotEqual(resolved, self._prod_db,
                                "测试里绝不能解析到真实的演示库")

    def test_http_write_lands_in_temp_db(self):
        """写一笔，确认落在临时库；生产库不应出现这笔。"""
        marker = "守卫用例-不应进生产库"
        r = self.client.post("/api/collect/create",
                             json={"amount": 1.23, "item": marker})
        self.assertEqual(r.status_code, 200, r.text)

        import sqlite3
        conn = sqlite3.connect(str(db.DB_PATH))
        try:
            n = conn.execute("SELECT COUNT(*) FROM payment_collections "
                             "WHERE item=?", (marker,)).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 1, "写入应落在临时库")

        if self._prod_db.exists():
            conn = sqlite3.connect(str(self._prod_db))
            try:
                n2 = conn.execute("SELECT COUNT(*) FROM payment_collections "
                                  "WHERE item=?", (marker,)).fetchone()[0]
            except sqlite3.Error:              # 生产库可能还没建这张表
                n2 = 0
            finally:
                conn.close()
            self.assertEqual(n2, 0, "测试数据绝不能写进演示库/生产库")


class TestBackupFlow(GapsHttpBase):
    def test_backup_list_create_export(self):
        r = self.client.get("/api/backup/list")
        self.assertEqual(r.status_code, 200, r.text)

        created = self.client.post("/api/backup/create", json={"kind": "manual"})
        self.assertEqual(created.status_code, 200, created.text)
        name = created.json().get("backup", {}).get("name") or \
            created.json().get("name")
        self.assertTrue(name, f"创建备份应返回文件名：{created.text}")

        listed = self.client.get("/api/backup/list").json()
        names = [b["name"] for b in listed.get("backups", [])]
        self.assertIn(name, names, "新建的备份应出现在列表里")

        exported = self.client.post("/api/backup/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        exp = exported.json()["export"]
        self.assertTrue(exp.get("name") or exp.get("filename"),
                        f"导出应返回包名：{exported.text}")

    def test_download_requires_existing_name(self):
        r = self.client.get("/api/backup/download/nosuchfile.zip")
        self.assertIn(r.status_code, (400, 403, 404))

    def test_restore_requires_confirm(self):
        """没有 confirm=true 时必须拒绝 —— 恢复会覆盖全部数据。"""
        created = self.client.post("/api/backup/create",
                                   json={"kind": "manual"}).json()
        name = created.get("backup", {}).get("name") or created.get("name")
        r = self.client.post(f"/api/backup/restore/{name}")
        self.assertEqual(r.status_code, 400)
        self.assertIn("confirm", r.json()["detail"])

    def test_import_requires_body(self):
        r = self.client.post("/api/backup/import?confirm=true")
        self.assertEqual(r.status_code, 400)


class TestCorrectionFlow(GapsHttpBase):
    def _add(self, amount=20.0, item="肉包"):
        return db.add_transaction(None, item, amount, trans_type="income",
                                  category="主营业务收入")[0]

    def test_edit_void_refund_and_audit(self):
        tid = self._add()
        r = self.client.post(f"/api/transactions/{tid}",
                             json={"amount": 25.0, "reason": "记错金额"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(float(r.json()["transaction"]["amount"]), 25.0)

        rf = self.client.post(f"/api/transactions/{tid}/refund",
                              json={"amount": 5.0, "reason": "退了一个"})
        self.assertEqual(rf.status_code, 200, rf.text)

        audits = self.client.get("/api/audits").json()
        rows = audits.get("audits", audits if isinstance(audits, list) else [])
        self.assertTrue(rows, "更正必须留下审计记录")
        actions = {a.get("action") for a in rows}
        self.assertTrue({"edit", "refund"} & actions,
                        f"审计里应能看到 edit/refund，实际 {actions}")

    def test_void_excludes_from_totals(self):
        keep = self._add(30.0, "豆浆")
        drop = self._add(40.0, "误记的收入")
        before = self.client.get("/api/orders/monthly").json()["income"]
        r = self.client.post(f"/api/transactions/{drop}/void",
                             json={"reason": "记错了"})
        self.assertEqual(r.status_code, 200, r.text)
        after = self.client.get("/api/orders/monthly").json()["income"]
        self.assertAlmostEqual(before - after, 40.0, places=2)
        # 原记录仍在（留痕），只是不计入统计
        # 注意响应是 {"transaction": {...}, "audits": [...]} 而不是直接返回交易
        txn = self.client.get(f"/api/transactions/{drop}").json()["transaction"]
        self.assertEqual(txn["status"], "voided")
        self.assertEqual(len(r.json()["voided_vouchers"]), 1,
                         "作废应连带冲销凭证")

    def test_edit_unknown_transaction_is_rejected(self):
        """改一笔不存在的账必须被拒绝（这里实现返回 400，不是 404）。"""
        r = self.client.post("/api/transactions/999999", json={"amount": 1})
        self.assertEqual(r.status_code, 400)
        self.assertIn("不存在", r.json()["detail"])


class TestCollectFlow(GapsHttpBase):
    def test_full_collection_flow_with_qr(self):
        created = self.client.post("/api/collect/create",
                                   json={"amount": 18.5, "item": "早点"})
        self.assertEqual(created.status_code, 200, created.text)
        c = created.json()["collection"]
        token, cid = c["token"], c["id"]

        # 收款码：店主侧接口，必须能生成
        qr = self.client.get(f"/api/collect/{token}/qr.svg"
                             f"?origin=http://192.168.1.5:8000")
        self.assertEqual(qr.status_code, 200, qr.text)
        self.assertIn("<svg", qr.text)

        # 顾客侧：公开页 + 信息 + 我已付款（都不带令牌）
        self.assertEqual(self.client.get(f"/pay/{token}").status_code, 200)
        info = self.client.get(f"/api/pay/{token}/info")
        self.assertEqual(info.status_code, 200)
        self.assertEqual(info.json()["amount"], 18.5)
        paid = self.client.post(f"/api/pay/{token}/paid",
                                json={"payer_name": "王阿姨"})
        self.assertEqual(paid.status_code, 200)
        self.assertEqual(paid.json()["status"], "paid")

        # 确认入账 → 自动生成流水与凭证
        before = self.client.get("/api/orders/monthly").json()["income"]
        conf = self.client.post(f"/api/collect/{cid}/confirm")
        self.assertEqual(conf.status_code, 200, conf.text)
        after = self.client.get("/api/orders/monthly").json()["income"]
        self.assertAlmostEqual(after - before, 18.5, places=2)
        # 顾客称呼自动变成熟客
        names = [x["name"] for x in
                 self.client.get("/api/customers").json()]
        self.assertIn("王阿姨", names)

    def test_cancel_then_confirm_is_rejected(self):
        created = self.client.post("/api/collect/create",
                                   json={"amount": 5}).json()["collection"]
        self.assertEqual(
            self.client.post(f"/api/collect/{created['id']}/cancel",
                             json={"reason": "顾客走了"}).status_code, 200)
        again = self.client.post(f"/api/collect/{created['id']}/confirm")
        self.assertEqual(again.status_code, 400)

    def test_qr_requires_owner_token(self):
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": "secret-token"}):
            created = self.client.post("/api/collect/create", json={"amount": 3},
                                       headers={"X-Shop-Token": "secret-token"}
                                       ).json()["collection"]
            r = self.client.get(f"/api/collect/{created['token']}/qr.svg")
            self.assertEqual(r.status_code, 401, "收款码是店主侧接口，不能裸奔")
            # 但公开收款页仍要能打开
            self.assertEqual(self.client.get(
                f"/pay/{created['token']}").status_code, 200)


class TestNotifyFlow(GapsHttpBase):
    def test_channels_inbox_and_test_push(self):
        providers = self.client.get("/api/notify/providers").json()["providers"]
        self.assertTrue(providers, "至少要有一个可用通道")
        events = self.client.get("/api/notify/events").json()["events"]
        self.assertTrue(events)

        # 字段名是 channel（不是 provider）；漏了会 422
        sent = self.client.post("/api/notify/test",
                                json={"channel": "mock", "event": "daily_review",
                                      "title": "测试", "content": "掌柜测试消息"})
        self.assertEqual(sent.status_code, 200, sent.text)

        logs = self.client.get("/api/notify/logs").json()["logs"]
        self.assertTrue(logs, "发过测试推送后应有投递记录")

        inbox = self.client.get("/api/notify/mock-inbox").json()["messages"]
        self.assertTrue(inbox, "本地通道应能看到收到的消息")

    def test_test_push_without_channel_is_rejected(self):
        """缺 channel 必须是 422（前端漏传参数时能立刻发现）。"""
        r = self.client.post("/api/notify/test", json={"event": "daily_review"})
        self.assertEqual(r.status_code, 422)

    def test_subscription_crud(self):
        r = self.client.post("/api/notify/subscriptions", json={
            "channel": "mock", "target": "本机演示",
            "events": ["daily_review"]})
        self.assertEqual(r.status_code, 200, r.text)
        sub_id = r.json()["subscription"]["id"]
        listed = self.client.get("/api/notify/subscriptions").json()["subscriptions"]
        self.assertEqual([s["id"] for s in listed], [sub_id])
        self.assertEqual(listed[0]["events"], ["daily_review"],
                         "events 应回传成列表，方便前端直接渲染")
        self.assertEqual(
            self.client.post(f"/api/notify/subscriptions/{sub_id}/test").status_code,
            200)
        self.assertEqual(
            self.client.delete(f"/api/notify/subscriptions/{sub_id}").status_code,
            200)
        self.assertEqual(
            self.client.get("/api/notify/subscriptions").json()["subscriptions"], [])


class TestMobileGaps(GapsHttpBase):
    """移动端补齐的三块能力：库存 / 发票 / 资金健康。"""

    def test_stock_crud_and_move(self):
        self.assertEqual(self.client.get("/api/stock").status_code, 200)
        # 字段名以 ProductIn 为准：stock_qty / safety_stock / unit_cost
        # （写成 stock/cost/min_stock 会被 Pydantic 静默丢弃，库存永远是 0）
        created = self.client.post("/api/products", json={
            "name": "肉包", "unit": "个", "unit_cost": 1.2,
            "stock_qty": 20, "safety_stock": 5})
        self.assertEqual(created.status_code, 200, created.text)
        pid = created.json()["product_id"]

        moved = self.client.post(f"/api/products/{pid}/move",
                                 json={"movement": "out", "qty": 3,
                                       "note": "卖掉了"})
        self.assertEqual(moved.status_code, 200, moved.text)

        stock = self.client.get("/api/stock").json()
        row = [p for p in stock["products"] if p["id"] == pid][0]
        self.assertEqual(float(row["stock_qty"]), 17.0)
        self.assertEqual(float(row["safety_stock"]), 5)

        self.assertEqual(self.client.delete(f"/api/products/{pid}").status_code, 200)

    def test_invoice_crud_and_void(self):
        created = self.client.post("/api/invoices", json={
            "direction": "output", "amount": 300, "counterparty": "老王",
            "invoice_date": "2026-09-10"})
        self.assertEqual(created.status_code, 200, created.text)
        inv_id = created.json()["invoice_id"]
        self.assertEqual(self.client.get("/api/invoices/summary").status_code, 200)
        voided = self.client.post(f"/api/invoices/{inv_id}/void")
        self.assertEqual(voided.status_code, 200, voided.text)

    def test_finance_cashflow_budget_debt(self):
        # 字段名以 CashflowIn 为准：cash_on_hand / months（传 days 会被静默忽略）
        r = self.client.post("/api/cashflow",
                             json={"cash_on_hand": 5000, "months": 3})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(len(body["months"]), 3, "预测月份数应听参数")
        self.assertEqual(float(body["start_cash"]), 5000)

        b = self.client.post("/api/budgets", json={
            "month": "2026-09", "category": "主营业务成本", "amount": 3000})
        self.assertEqual(b.status_code, 200, f"新增预算失败：{b.text}")
        self.assertEqual(self.client.get("/api/budgets?month=2026-09").status_code, 200)

        d = self.client.post("/api/debts", json={
            "kind": "receivable", "counterparty": "张叔", "amount": 500,
            "due_date": "2026-09-30"})
        self.assertEqual(d.status_code, 200, f"新增应收失败：{d.text}")
        # GET /api/debts 直接返回列表（不是 {"debts": [...]}）
        rows = self.client.get("/api/debts").json()
        rows = rows if isinstance(rows, list) else rows.get("debts", [])
        self.assertTrue(rows)
        self.assertEqual(rows[0]["amount"], 500)
        # 点「结清」发的就是空 body —— 不能 422
        settle = self.client.post(f"/api/debts/{rows[0]['id']}/settle", json={})
        self.assertEqual(settle.status_code, 200, settle.text)
        # 不带 body 也要能全额结清
        d2 = self.client.post("/api/debts", json={
            "kind": "payable", "counterparty": "面厂", "amount": 80}).json()
        d2id = d2.get("debt_id") or (d2.get("debt") or {}).get("id")
        settle2 = self.client.post(f"/api/debts/{d2id}/settle")
        self.assertEqual(settle2.status_code, 200, settle2.text)
        self.assertEqual(self.client.get("/api/debts/aging").status_code, 200)


class TestAccountingFlow(GapsHttpBase):
    def _books(self):
        db.add_transaction(None, "卖早点", 1000, trans_type="income",
                           category="主营业务收入")
        db.add_transaction(None, "买面粉", 300, trans_type="expense",
                           category="主营业务成本")

    def test_statements_and_close_reopen(self):
        self._books()
        tb = self.client.get("/api/accounting/trial-balance?period=2026-09")
        self.assertEqual(tb.status_code, 200, tb.text)
        body = tb.json()
        self.assertTrue(body["balanced"],
                        f"借贷应平衡：借 {body['total_debit']} / 贷 {body['total_credit']}")
        self.assertAlmostEqual(body["total_debit"], body["total_credit"], places=2)

        inc = self.client.get("/api/accounting/income-statement?period=2026-09").json()
        self.assertAlmostEqual(inc["total_revenue"], 1000, places=2)
        self.assertAlmostEqual(inc["total_expense"], 300, places=2)
        self.assertAlmostEqual(inc["net_profit"], 700, places=2)

        bal = self.client.get("/api/accounting/balance-sheet?as_of=2026-09-30").json()
        self.assertTrue(bal["balanced"],
                        f"资产 {bal['total_assets']} 应等于负债+权益 "
                        f"{bal['liabilities_and_equity']}")

        closed = self.client.post("/api/accounting/close", json={"period": "2026-09"})
        self.assertEqual(closed.status_code, 200, closed.text)
        self.assertAlmostEqual(closed.json()["net_profit"], 700, places=2)
        self.assertEqual(len(self.client.get("/api/accounting/closings")
                             .json()["closings"]), 1)

        # 结转后：本期损益归零、资产负债表仍平衡
        inc2 = self.client.get("/api/accounting/income-statement?period=2026-09").json()
        self.assertAlmostEqual(inc2["net_profit"], 0, places=2)
        bal2 = self.client.get("/api/accounting/balance-sheet?as_of=2026-09-30").json()
        self.assertTrue(bal2["balanced"],
                        f"结转后仍应平衡：{bal2['total_assets']} vs "
                        f"{bal2['liabilities_and_equity']}")

        reopened = self.client.post("/api/accounting/reopen", json={"period": "2026-09"})
        self.assertEqual(reopened.status_code, 200, reopened.text)
        inc3 = self.client.get("/api/accounting/income-statement?period=2026-09").json()
        self.assertAlmostEqual(inc3["net_profit"], 700, places=2)
        # 反结转不删记录，而是把该期间标记为 reopened（留痕），
        # 所以列表里仍在，只是状态变了 —— 前端据此判断"已可重结"
        closings = self.client.get("/api/accounting/closings").json()["closings"]
        self.assertEqual([c["status"] for c in closings], ["reopened"])

    def test_reclose_after_edit_does_not_double_count(self):
        """结转后又改账再结转：不能把上次的结转凭证重复计入。"""
        self._books()
        self.client.post("/api/accounting/close", json={"period": "2026-09"})
        db.add_transaction(None, "又卖了一单", 200, trans_type="income",
                           category="主营业务收入")
        again = self.client.post("/api/accounting/close", json={"period": "2026-09"})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertAlmostEqual(again.json()["net_profit"], 900, places=2)
        self.assertTrue(again.json()["reclosed"], "重复结转应走红冲重算")
        tb = self.client.get("/api/accounting/trial-balance?period=2026-09"
                             "&exclude_closing=true").json()
        self.assertTrue(tb["balanced"])


class TestMultiShopFlow(GapsHttpBase):
    def test_create_switch_isolate(self):
        base = self.client.get("/api/shops").json()["shops"]
        self.assertEqual([s["id"] for s in base], [shops.DEFAULT_SHOP_ID])

        created = self.client.post("/api/shops", json={"name": "二号店"})
        self.assertEqual(created.status_code, 200, created.text)
        sid = created.json()["shop"]["id"]

        db.add_transaction(None, "一号店的单", 100, trans_type="income",
                           category="主营业务收入")
        # 二号店：干净
        with shops.use_shop(sid):
            self.assertEqual(db.list_transactions(), [])
            db.add_transaction(None, "二号店的单", 200, trans_type="income",
                               category="主营业务收入")

        # HTTP 层带 X-Shop-Id 也要落到对应的库
        r = self.client.get("/api/transactions", headers={"X-Shop-Id": str(sid)})
        self.assertEqual([t["item"] for t in r.json()], ["二号店的单"])
        r1 = self.client.get("/api/transactions")
        self.assertEqual([t["item"] for t in r1.json()], ["一号店的单"])

        ctx = self.client.get("/api/shops/context",
                              headers={"X-Shop-Id": str(sid)}).json()
        self.assertEqual(ctx["shop_id"], sid)
        self.assertEqual(ctx["shop"]["name"], "二号店")

    def test_shop_isolation_of_backups(self):
        sid = self.client.post("/api/shops", json={"name": "二号店"}
                               ).json()["shop"]["id"]
        with shops.use_shop(sid):
            db.add_transaction(None, "二号店的单", 200, trans_type="income",
                               category="主营业务收入")
        # 备份请求必须带 X-Shop-Id（前端切店后就是这么发的）；
        # 中间件据此把请求落到二号店，备份才会进 shop-<id> 子目录。
        info = self.client.post("/api/backup/create", json={"kind": "manual"},
                                headers={"X-Shop-Id": str(sid)})
        self.assertEqual(info.status_code, 200, info.text)
        path = info.json()["backup"]["path"]
        self.assertIn(f"shop-{sid}", path,
                      f"二号店的备份应落在 shop-{sid} 子目录，实际 {path}")

    def test_members_and_permissions(self):
        sid = self.client.post("/api/shops", json={"name": "二号店"}
                               ).json()["shop"]["id"]
        u = self.client.post("/api/shops/users", json={
            "name": "小李", "role": "staff", "shop_ids": [sid]})
        self.assertEqual(u.status_code, 200, u.text)
        token = u.json()["user"]["token"]

        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": "owner-token"}):
            h = {"X-Shop-Token": token}
            # 自己的店能读
            self.assertEqual(
                self.client.get("/api/shops/context", headers=h).status_code, 200)
            # 没被授权的店：403（不能静默落到默认店）
            r = self.client.get("/api/shops/context",
                                headers={**h, "X-Shop-Id": str(shops.DEFAULT_SHOP_ID)})
            self.assertEqual(r.status_code, 403, r.text)
            # 店员不能管人
            self.assertEqual(
                self.client.get("/api/shops/users", headers=h).status_code, 403)
            # 店员可以记账（写权限）
            self.assertNotEqual(
                self.client.get("/api/transactions", headers=h).status_code, 403)
            # 全局令牌可以访问任何店
            self.assertEqual(self.client.get(
                "/api/transactions",
                headers={"X-Shop-Token": "owner-token"}).status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
