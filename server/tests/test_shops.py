"""tests/test_shops.py — 多店 / 多用户（店铺注册表 + 店上下文 + 角色权限）

覆盖：
  1. 注册表初始化幂等、默认店存在
  2. 一店一库：新建店自动建标准业务库，且各店数据互不串
  3. 店上下文：不设上下文 == 单店旧行为（这是 404 个既有测试的前提）
  4. 建库不污染调用方（DB_PATH_OVERRIDE 必须复原 —— 否则会把数据写进新店）
  5. 用户/令牌/角色、成员关系、越权拒绝
  6. 删店保护默认店；purge 才真删文件
  7. 备份跟随店上下文（A 店备份不能存下 B 店的库）
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import auth
import backup
import config
import db
import shops


class ShopTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._orig = (config.DATA_DIR, config.DB_PATH,
                      shops.SHOPS_DIR, shops.REGISTRY_PATH)
        config.DATA_DIR = str(root)
        config.DB_PATH = str(root / "ai_shopkeeper.db")
        shops.SHOPS_DIR = root / "shops"
        shops.REGISTRY_PATH = root / "registry.db"
        self.root = root
        # 数据层也要重定向：db.DB_PATH 是 import 时就绑定的模块变量，
        # 只改 config.DB_PATH 是拦不住 db.get_conn() 的 —— 实测会把测试数据
        # 写进真实的演示库（差点污染演示数据）。
        db.DB_PATH = Path(config.DB_PATH)
        self.ctx_tokens = []
        shops.init_registry()

    def tearDown(self):
        self.reset_ctx()
        config.DATA_DIR, config.DB_PATH, shops.SHOPS_DIR, shops.REGISTRY_PATH = self._orig
        db._schema_ready.clear()
        self._tmp.cleanup()

    # ---- 上下文工具 ----
    def use_shop(self, shop_id):
        self.ctx_tokens.append(shops.set_current_shop(shop_id))

    def reset_ctx(self):
        while self.ctx_tokens:
            shops.reset_current_shop(self.ctx_tokens.pop())
        shops.set_current_shop(None)

    def add_income(self, item, amount):
        """直接写数据层（不走 AI 解析，测试不依赖网络/缓存）。"""
        return db.add_transaction(None, item, amount, trans_type="income",
                                  category="主营业务收入")


class TestRegistry(ShopTestBase):
    def test_init_is_idempotent_and_creates_default_shop(self):
        shops.init_registry()
        shops.init_registry()
        all_shops = shops.list_shops()
        self.assertEqual([s["id"] for s in all_shops], [shops.DEFAULT_SHOP_ID])
        self.assertIn("默认店", all_shops[0]["name"])

    def test_default_shop_points_to_legacy_db(self):
        """默认店必须复用老库文件，否则升级后老数据"消失"。"""
        self.assertEqual(shops.db_path_for(shops.DEFAULT_SHOP_ID),
                         Path(config.DB_PATH))
        self.assertEqual(shops.get_shop(1)["db_file"], str(config.DB_PATH))

    def test_create_shop_builds_standard_schema(self):
        shop = shops.create_shop("二号店")
        self.assertNotEqual(shop["id"], shops.DEFAULT_SHOP_ID)
        path = shops.db_path_for(shop["id"])
        self.assertTrue(path.exists())
        self.assertEqual(shop["db_file"], str(path))
        # 标准业务库：关键表必须都在（复用项目 init_db 流程）
        import sqlite3
        conn = sqlite3.connect(str(path))
        try:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        finally:
            conn.close()
        for table in ("customers", "transactions", "vouchers", "products",
                      "invoices", "opening_balances"):
            self.assertIn(table, names, f"新店缺表：{table}")

    def test_update_shop_rejects_bad_status(self):
        shop = shops.create_shop("三号店")
        with self.assertRaises(ValueError):
            shops.update_shop(shop["id"], status="nonsense")
        self.assertEqual(shops.update_shop(shop["id"], name="三号店(改)")["name"],
                         "三号店(改)")

    def test_delete_default_shop_is_refused(self):
        with self.assertRaises(ValueError):
            shops.delete_shop(shops.DEFAULT_SHOP_ID)
        self.assertIsNotNone(shops.get_shop(shops.DEFAULT_SHOP_ID))

    def test_delete_shop_keeps_file_unless_purge(self):
        shop = shops.create_shop("临时店")
        path = shops.db_path_for(shop["id"])
        self.assertTrue(path.exists())
        r = shops.delete_shop(shop["id"])
        self.assertFalse(r["purged"])
        self.assertTrue(path.exists(), "默认可恢复：文件应保留")
        self.assertIsNone(shops.get_shop(shop["id"]))

        shop2 = shops.create_shop("彻底删")
        path2 = shops.db_path_for(shop2["id"])
        r2 = shops.delete_shop(shop2["id"], purge=True)
        self.assertTrue(r2["purged"])
        self.assertFalse(path2.exists())

    def test_create_shop_copy_from(self):
        import sqlite3
        src = shops.create_shop("母店")
        with shops.use_shop(src["id"]):
            self.add_income("测试", 88)
        dst = shops.create_shop("直营店", copy_from=src["id"])
        # 母店的数据不会被复制过来（copy_from 只复制结构）
        with shops.use_shop(dst["id"]):
            self.assertEqual(db.list_transactions(), [])
        conn = sqlite3.connect(str(shops.db_path_for(dst["id"])))
        try:
            n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 0, "copy_from 只应复制 schema，不应带业务数据")


class TestShopContext(ShopTestBase):
    def test_no_context_falls_back_to_default_db(self):
        """单店兼容性：没有店上下文时，路径必须还是 db.DB_PATH。"""
        self.assertIsNone(shops.current_shop_id())
        self.assertIsNone(shops.resolve_db_path())
        self.assertEqual(Path(db.current_db_path()), Path(db.DB_PATH))

    def test_default_shop_context_maps_to_default_db(self):
        self.use_shop(shops.DEFAULT_SHOP_ID)
        self.assertEqual(Path(db.current_db_path()), Path(config.DB_PATH))

    def test_data_is_isolated_between_shops(self):
        a = shops.DEFAULT_SHOP_ID
        b = shops.create_shop("B 店")["id"]
        with shops.use_shop(a):
            self.add_income("A店单", 100)
        with shops.use_shop(b):
            self.add_income("B店单", 200)
        with shops.use_shop(a):
            items = [t["item"] for t in db.list_transactions()]
            self.assertEqual(items, ["A店单"])
        with shops.use_shop(b):
            items = [t["item"] for t in db.list_transactions()]
            self.assertEqual(items, ["B店单"])

    def test_creating_shop_does_not_leak_override(self):
        """建店内部会临时覆盖库路径；若忘记复原，调用方后续写入会跑进新店。"""
        shops.create_shop("临时")
        self.assertIsNone(db.DB_PATH_OVERRIDE)
        self.add_income("默认店单", 10)
        self.assertIsNone(shops.current_shop_id())
        self.assertEqual([t["item"] for t in db.list_transactions()], ["默认店单"])
        # 老库（默认店）里确实有这笔，而新店是干净的
        self.assertEqual(Path(db.DB_PATH), Path(config.DB_PATH))

    def test_contextvar_independent_per_context(self):
        """contextvars 语义：子上下文里设店，父上下文不受影响。"""
        import contextvars
        b = shops.create_shop("B 店")["id"]

        def child():
            tok = shops.set_current_shop(b)
            try:
                return shops.current_shop_id()
            finally:
                shops.reset_current_shop(tok)

        ctx = contextvars.copy_context()
        self.assertEqual(ctx.run(child), b)
        self.assertIsNone(shops.current_shop_id())


class TestUsersAndRoles(ShopTestBase):
    def test_create_user_returns_token_once_and_lists_prefix(self):
        u = shops.create_user("小李", role="staff", shop_ids=[shops.DEFAULT_SHOP_ID])
        self.assertTrue(u["token"])
        listed = shops.list_users()
        self.assertEqual(len(listed), 1)
        self.assertNotIn("token", listed[0], "列表接口不应回传完整令牌")
        self.assertEqual(listed[0]["token_prefix"], u["token"][:8])
        self.assertEqual(listed[0]["shop_ids"], [shops.DEFAULT_SHOP_ID])

    def test_find_user_by_token_filters_disabled(self):
        u = shops.create_user("小王", role="staff")
        self.assertIsNotNone(shops.find_user_by_token(u["token"]))
        shops.update_user(u["id"], status="disabled")
        self.assertIsNone(shops.find_user_by_token(u["token"]),
                          "停用账号的令牌必须失效")

    def test_create_user_validates(self):
        with self.assertRaises(ValueError):
            shops.create_user("", role="staff")
        with self.assertRaises(ValueError):
            shops.create_user("小张", role="boss")
        with self.assertRaises(ValueError):
            shops.create_user("小张", role="staff", shop_ids=[999])

    def test_role_permissions(self):
        self.assertTrue(shops.has_permission("owner", "manage_shop"))
        self.assertTrue(shops.has_permission("admin", "manage_user"))
        self.assertFalse(shops.has_permission("admin", "manage_shop"))
        self.assertTrue(shops.has_permission("staff", "write"))
        self.assertFalse(shops.has_permission("staff", "manage_user"))

    def test_grant_revoke_and_shop_users(self):
        u = shops.create_user("小二", role="staff")
        self.assertEqual(shops.user_shops(u["id"]), [])
        shops.grant(shops.DEFAULT_SHOP_ID, u["id"], "admin")
        members = shops.shop_users(shops.DEFAULT_SHOP_ID)
        self.assertEqual([m["name"] for m in members], ["小二"])
        self.assertEqual(members[0]["role"], "admin")
        self.assertEqual([s["id"] for s in shops.user_shops(u["id"])],
                         [shops.DEFAULT_SHOP_ID])
        shops.revoke(shops.DEFAULT_SHOP_ID, u["id"])
        self.assertEqual(shops.shop_users(shops.DEFAULT_SHOP_ID), [])

    def test_grant_checks_existence(self):
        u = shops.create_user("小二")
        with self.assertRaises(ValueError):
            shops.grant(999, u["id"])
        with self.assertRaises(ValueError):
            shops.grant(shops.DEFAULT_SHOP_ID, 999)

    def test_delete_user_cascades_membership(self):
        u = shops.create_user("临时工", shop_ids=[shops.DEFAULT_SHOP_ID])
        shops.delete_user(u["id"])
        self.assertEqual(shops.shop_users(shops.DEFAULT_SHOP_ID), [])

    def test_set_default_shop_requires_membership(self):
        u = shops.create_user("小陈")
        b = shops.create_shop("B 店")["id"]
        with self.assertRaises(ValueError):
            shops.set_default_shop(u["id"], b)
        shops.grant(b, u["id"], "staff")
        shops.set_default_shop(u["id"], b)
        self.assertEqual(shops.find_user_by_token(u["token"])["default_shop_id"], b)

    def test_create_user_with_shops_sets_default(self):
        u = shops.create_user("小赵", shop_ids=[shops.DEFAULT_SHOP_ID])
        self.assertEqual(shops.find_user_by_token(u["token"])["default_shop_id"],
                         shops.DEFAULT_SHOP_ID)


class TestBackupFollowsShop(ShopTestBase):
    def test_backup_dir_is_per_shop(self):
        base = Path(config.DATA_DIR) / "backups"
        self.assertEqual(backup._backup_dir(), base)
        with shops.use_shop(shops.DEFAULT_SHOP_ID):
            self.assertEqual(backup._backup_dir(), base)
        b = shops.create_shop("B 店")["id"]
        with shops.use_shop(b):
            self.assertEqual(backup._backup_dir(), base / f"shop-{b}")
            self.assertEqual(backup._db_path(), shops.db_path_for(b))

    def test_backup_contains_only_current_shop_data(self):
        a = shops.DEFAULT_SHOP_ID
        b = shops.create_shop("B 店")["id"]
        with shops.use_shop(a):
            self.add_income("A店单", 100)
        with shops.use_shop(b):
            self.add_income("B店单", 200)

        import sqlite3
        with shops.use_shop(b):
            info = backup.create_backup("manual")
            conn = sqlite3.connect(info["path"] if isinstance(info, dict)
                                   else str(info))
            try:
                items = [r[0] for r in conn.execute(
                    "SELECT item FROM transactions").fetchall()]
            finally:
                conn.close()
        self.assertEqual(items, ["B店单"],
                         "B 店备份里出现了 A 店数据 —— 备份没跟随店上下文")


class TestShopHTTP(ShopTestBase):
    """HTTP 层：令牌 → 用户 → 店上下文 → 权限。"""

    def setUp(self):
        super().setUp()
        db.DB_PATH = Path(config.DB_PATH)
        db.init_db()
        from fastapi.testclient import TestClient
        import main
        self.client = TestClient(main.app)
        self._env = auth.get_configured_token()

    def tearDown(self):
        import os
        if self._env:
            os.environ[auth.ENV_TOKEN] = self._env
        else:
            os.environ.pop(auth.ENV_TOKEN, None)
        super().tearDown()

    def _set_owner_token(self, token):
        import os
        os.environ[auth.ENV_TOKEN] = token

    def test_context_uses_default_shop_when_untouched(self):
        r = self.client.get("/api/shops/context")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["shop_id"], shops.DEFAULT_SHOP_ID)

    def test_create_shop_and_list_via_owner_token(self):
        self._set_owner_token("owner-token-abc")
        r = self.client.post("/api/shops", json={"name": "二号店"},
                             headers={"X-Shop-Token": "owner-token-abc"})
        self.assertEqual(r.status_code, 200, r.text)
        new_id = r.json()["shop"]["id"]
        r2 = self.client.get("/api/shops",
                             headers={"X-Shop-Token": "owner-token-abc"})
        self.assertEqual([s["id"] for s in r2.json()["shops"]],
                         [shops.DEFAULT_SHOP_ID, new_id])

    def test_owner_requires_token_when_configured(self):
        self._set_owner_token("owner-token-abc")
        r = self.client.get("/api/shops/context")
        self.assertEqual(r.status_code, 401)

    def test_user_token_restricts_to_own_shops(self):
        self._set_owner_token("owner-token-abc")
        other = shops.create_shop("别人的店")["id"]
        u = shops.create_user("小李", role="staff",
                              shop_ids=[shops.DEFAULT_SHOP_ID])
        h = {"X-Shop-Token": u["token"]}

        # 自己的店：能读
        r = self.client.get("/api/shops/context", headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["shop_id"], shops.DEFAULT_SHOP_ID)

        # 别人的店：明确拒绝，不能静默落到默认店（否则是数据泄露）
        r2 = self.client.get("/api/shops/context",
                             headers={**h, "X-Shop-Id": str(other)})
        self.assertEqual(r2.status_code, 403, r2.text)
        self.assertIn("无权访问店铺", r2.json()["detail"])

    def test_staff_cannot_manage_users(self):
        self._set_owner_token("owner-token-abc")
        u = shops.create_user("小李", role="staff",
                              shop_ids=[shops.DEFAULT_SHOP_ID])
        r = self.client.get("/api/shops/users", headers={"X-Shop-Token": u["token"]})
        self.assertEqual(r.status_code, 403)
        self.assertIn("manage_user", r.json()["detail"])

        admin = shops.create_user("店长", role="admin",
                                  shop_ids=[shops.DEFAULT_SHOP_ID])
        r2 = self.client.get("/api/shops/users",
                             headers={"X-Shop-Token": admin["token"]})
        self.assertEqual(r2.status_code, 200, r2.text)

    def test_admin_cannot_create_shop(self):
        self._set_owner_token("owner-token-abc")
        admin = shops.create_user("店长", role="admin",
                                  shop_ids=[shops.DEFAULT_SHOP_ID])
        r = self.client.post("/api/shops", json={"name": "新店"},
                             headers={"X-Shop-Token": admin["token"]})
        self.assertEqual(r.status_code, 403)

    def test_switch_shop_requires_membership(self):
        self._set_owner_token("owner-token-abc")
        b = shops.create_shop("B 店")["id"]
        u = shops.create_user("小周", role="staff", shop_ids=[shops.DEFAULT_SHOP_ID])
        h = {"X-Shop-Token": u["token"]}
        r = self.client.post("/api/shops/switch", json={"shop_id": b}, headers=h)
        self.assertEqual(r.status_code, 403)
        r2 = self.client.post("/api/shops/switch",
                              json={"shop_id": shops.DEFAULT_SHOP_ID}, headers=h)
        self.assertEqual(r2.status_code, 200, r2.text)

    def test_business_api_follows_shop_header(self):
        """关键闭环：带 X-Shop-Id 记账，数据必须落在那家店。

        起的是真实 app（含中间件），只把 AI 解析打桩（不联网、不耗额度）。
        """
        from unittest import mock
        self._set_owner_token("owner-token-abc")
        b = shops.create_shop("B 店")["id"]
        h = {"X-Shop-Token": "owner-token-abc", "X-Shop-Id": str(b)}
        fake = {"customer": "", "item": "B店单", "amount": 66, "note": "",
                "tags": "", "category": "主营业务收入", "trans_type": "income"}
        with mock.patch("ai.parse_transaction", return_value=fake):
            r = self.client.post("/api/orders", json={"text": "B店单 66 元"}, headers=h)
            self.assertEqual(r.status_code, 200, r.text)

            # A 店（默认店）看不到
            r2 = self.client.get("/api/transactions",
                                 headers={"X-Shop-Token": "owner-token-abc"})
            self.assertEqual(r2.status_code, 200, r2.text)
            self.assertEqual(r2.json(), [])

            # B 店能看到
            r3 = self.client.get("/api/transactions", headers=h)
            self.assertEqual([t["item"] for t in r3.json()], ["B店单"])

    def test_delete_default_shop_via_http_is_400(self):
        self._set_owner_token("owner-token-abc")
        r = self.client.delete(f"/api/shops/{shops.DEFAULT_SHOP_ID}",
                               headers={"X-Shop-Token": "owner-token-abc"})
        self.assertEqual(r.status_code, 400)

    def test_public_pay_page_resolves_owning_shop(self):
        """顾客扫 B 店的收款码：没带任何令牌也要能打开，且打的是 B 店的数据。

        这是多店最容易漏的一处：公开页没有令牌可依据，
        若不按收款码 token 反查店铺，会落到默认店 → 顾客扫码打不开。
        """
        self._set_owner_token("owner-token-abc")
        b = shops.create_shop("B 店")["id"]
        h = {"X-Shop-Token": "owner-token-abc", "X-Shop-Id": str(b)}
        created = self.client.post("/api/collect/create",
                                   json={"amount": 12, "item": "B店豆浆"},
                                   headers=h).json()
        tok = created["collection"]["token"]

        # 顾客侧：不带任何令牌
        page = self.client.get(f"/pay/{tok}")
        self.assertEqual(page.status_code, 200, "公开收款页不该被鉴权拦")
        self.assertIn("<!DOCTYPE html>", page.text)

        info = self.client.get(f"/api/pay/{tok}/info")
        self.assertEqual(info.status_code, 200, info.text)
        # 公开接口是扁平结构，且刻意不暴露客户/分类等经营信息
        self.assertEqual(info.json()["amount"], 12)
        self.assertEqual(info.json()["item"], "B店豆浆")

    def test_public_pay_page_still_works_for_default_shop(self):
        """单店/演示场景：默认店的收款码照旧能打开（不能因多店改造而回归）。"""
        self._set_owner_token("owner-token-abc")
        created = self.client.post(
            "/api/collect/create", json={"amount": 9, "item": "茶叶蛋"},
            headers={"X-Shop-Token": "owner-token-abc"}).json()
        tok = created["collection"]["token"]
        info = self.client.get(f"/api/pay/{tok}/info")
        self.assertEqual(info.status_code, 200, info.text)
        self.assertEqual(info.json()["item"], "茶叶蛋")


if __name__ == "__main__":
    unittest.main()
