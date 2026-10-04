# -*- coding: utf-8 -*-
"""第二轮独立复核发现的缺陷的回归测试（每条都对应一个**实测过的**真缺陷）。

为什么单独一个文件：这些不是"新功能测试"，而是"翻车点档案"——
每条用例都写明当初怎么错的，防止以后回退。

运行：cd server && python -m pytest tests/test_knowledge_regressions.py -q
"""
import sys
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import db  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

try:
    import db_knowledge_schema  # noqa: E402
    import knowledge_assets as ka  # noqa: E402
    import knowledge_governance as kg  # noqa: E402
    import knowledge_verify as kv  # noqa: E402
    import shop_relations as sr  # noqa: E402
    _READY = True
except Exception:  # noqa: BLE001
    _READY = False


def _fresh_db():
    tmp = tempfile.TemporaryDirectory()
    db.DB_PATH = Path(tmp.name) / "regress.db"
    db._schema_ready.clear()
    with db.get_conn() as conn:
        db_knowledge_schema.init_knowledge_tables(conn)
    return tmp


def _client():
    app = FastAPI()
    from routers import knowledge, knowledge_assets, relations
    app.include_router(knowledge.router)
    app.include_router(knowledge_assets.router)
    app.include_router(relations.router)
    return TestClient(app, raise_server_exceptions=False)


@unittest.skipUnless(_READY, "知识/关系层尚未就绪")
class TestRelationDomainDegrades(unittest.TestCase):
    """【中-1】关系域曾直接 500：读不到表就整域不可用，重启才恢复。"""

    def setUp(self):
        self._tmp = _fresh_db()
        self.c = _client()

    def tearDown(self):
        self._tmp.cleanup()

    def test_unavailable_relation_layer_returns_200_not_500(self):
        boom = mock.Mock(side_effect=RuntimeError("no such table: knowledge_edges"))
        with mock.patch.object(sr, "relations_summary", boom), \
             mock.patch.object(sr, "entity_landscape", boom), \
             mock.patch.object(sr, "knowledge_chain", boom):
            for path in ("/api/relations/summary", "/api/relations/graph",
                         "/api/relations/chain?entity_type=customer&entity_ref=1"):
                r = self.c.get(path)
                self.assertEqual(r.status_code, 200, f"{path} 不该 500：{r.text}")
                self.assertFalse(r.json()["available"])
                self.assertIn("error", r.json())

    def test_invalid_entity_type_is_rejected(self):
        """【中-5】entity_type 曾是自由文本，非法值 200；现在必须 422。"""
        self.assertEqual(
            self.c.get("/api/relations/chain?entity_type=zzz").status_code, 422)
        self.assertEqual(
            self.c.get("/api/relations/chain?entity_type=customer").status_code, 200)

    def test_schema_ready_flag_rolls_back_on_migration_failure(self):
        """【中-1】迁移失败必须撤销 ready 标记，否则该进程此后永久跳过迁移。"""
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        db.DB_PATH = Path(tmp.name) / "migrate_fail.db"
        db._schema_ready.clear()
        with db.get_conn():                      # 先建好库
            pass
        with sqlite3.connect(str(db.DB_PATH)) as raw:
            raw.execute("PRAGMA user_version = 1")   # 造出"确实需要迁移"的状态
        # 让迁移在写版本号那一步失败（此时 DDL 已提交，最能体现"半途失败"）
        db._schema_ready.discard(str(db.DB_PATH))
        with mock.patch.object(db, "_raw_conn",
                               side_effect=sqlite3.OperationalError("disk I/O error")):
            with db.get_conn():
                pass
        self.assertNotIn(str(db.DB_PATH), db._schema_ready,
                         "迁移失败后不应把该库标记为已就绪")


@unittest.skipUnless(_READY, "知识/关系层尚未就绪")
class TestAssetGuards(unittest.TestCase):
    """【中-2】【中-4】【中低-7】登记入口的三个坑。"""

    def setUp(self):
        self._tmp = _fresh_db()

    def tearDown(self):
        self._tmp.cleanup()

    def test_overlong_statement_rejected_over_http(self):
        c = _client()
        r = c.post("/api/knowledge/assets", json={
            "kind": "fact", "subject": "s", "statement": "长" * 5000})
        self.assertEqual(r.status_code, 422, "正文必须有长度上限（原先 10 万字也能入库）")
        ok = c.post("/api/knowledge/assets", json={
            "kind": "fact", "subject": "s", "statement": "长" * 1500})
        self.assertEqual(ok.status_code, 200)

    def test_layer_truncates_even_when_called_directly(self):
        """内部调用（AI 抽取路径）绕过请求校验，也要在上限内落库。"""
        res = ka.register_asset("fact", "subject" * 100, "正文" * 3000, source_kind="snapshot")
        self.assertLessEqual(len(res["asset"]["statement"]), ka.STATEMENT_MAX_CHARS)
        self.assertLessEqual(len(res["asset"]["subject"]), ka.EVIDENCE_MAX_CHARS)

    def test_concurrent_register_never_yields_two_active_rows(self):
        """【中-4】并发登记曾造出两条 version=1 的 active 行。"""
        barrier = threading.Barrier(2, timeout=10)
        errors = []

        def worker(text):
            try:
                barrier.wait()
                ka.register_asset("fact", "race_key", text, source_kind="snapshot")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in ("并发内容1", "并发内容2")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rows = ka.list_assets(state=None, limit=50)
        active = [r for r in rows if r["asset_key"] == "fact:race_key"
                  and r["state"] in ("active", "draft")]
        self.assertLessEqual(len(active), 1, f"同一 asset_key 不许有两条在用行：{active}")
        self.assertFalse(errors, f"并发登记不该抛异常：{errors}")

    def test_draft_never_clobbers_active(self):
        """【中低-7】草稿曾把在用结论顶成 superseded，导致该 key 一条 active 都不剩。"""
        a = ka.register_asset("fact", "state_key", "结论一", source_kind="snapshot")
        self.assertEqual(a["asset"]["state"], "active")
        d = ka.register_asset("fact", "state_key", "草稿二", state="draft", source_kind="manual")
        self.assertFalse(d["created"], "在用结论在手时，草稿不该新增行")
        self.assertIn("草稿未落库", d["asset"].get("note", ""))
        still = [r for r in ka.list_assets(state=None, limit=50)
                 if r["asset_key"] == "fact:state_key" and r["state"] == "active"]
        self.assertEqual(len(still), 1, "草稿不能把在用结论顶掉")
        self.assertEqual(still[0]["asset_id"], a["asset"]["asset_id"])

    def test_draft_promotes_to_active_without_new_row(self):
        """draft→active 的升级要真的发生（原先静默忽略 state）。"""
        d = ka.register_asset("fact", "promote_key", "草稿内容", state="draft",
                              source_kind="manual")
        up = ka.register_asset("fact", "promote_key", "草稿内容", state="active",
                               source_kind="manual")
        self.assertFalse(up["created"])
        self.assertEqual(up["asset"]["state"], "active")
        self.assertEqual(up["asset"]["asset_id"], d["asset"]["asset_id"])


@unittest.skipUnless(_READY, "知识/关系层尚未就绪")
class TestVerifyAndGovernance(unittest.TestCase):
    """【中-3】【低-13】核验的写放大与错误码一致性。"""

    def setUp(self):
        self._tmp = _fresh_db()

    def tearDown(self):
        self._tmp.cleanup()

    def test_review_knowledge_only_verifies_what_it_shows(self):
        """【中-3】原先对全部 active 资产逐条核验并写库（200 条 → 12.4s + 200 次写）。"""
        for i in range(30):
            ka.register_asset("fact", f"n1_{i}", f"第 {i} 条",
                              evidence=[f"today_balance={i + 1}"], source_kind="snapshot")
        calls = {"n": 0}
        real = kv.build_verifiers

        def counting():
            calls["n"] += 1
            return real()

        with mock.patch.object(kv, "build_verifiers", side_effect=counting):
            brief = kg.review_knowledge(limit=5)
        self.assertEqual(len(brief["items"]), 5)
        self.assertLessEqual(calls["n"], 5, "只该核验要展示的那几条（先截断再核验）")

    def test_summary_verification_is_real_not_just_present(self):
        """【低-9】原先只断言 verification 非 None，核验全挂也绿。"""
        ka.register_asset("fact", "v1", "现金还能撑 2 个月",
                          evidence=["cash_runway_months=2"], source_kind="snapshot")
        v = kg.governance_summary(verify=True)["verification"]
        self.assertIsNotNone(v)
        self.assertNotIn("error", v, f"核验不应以降级错误对象返回：{v}")
        self.assertGreaterEqual(v["checked"], 1)

    def test_verify_missing_asset_is_404(self):
        """【低-13】不存在的 asset_id 曾返回 200 + error 字段，与 GET 的 404 不一致。"""
        c = _client()
        r = c.post("/api/knowledge/verify", json={"asset_id": "AS-19700101-999"})
        self.assertEqual(r.status_code, 404)

    def test_maintenance_prunes_old_bundles_and_edges(self):
        """【低-10】【低-12】知识包文件与失效边必须有保留期清理。"""
        import os
        import time as _time
        from knowledge_governance import EXPORT_PREFIX, _export_dir
        old = _export_dir() / f"{EXPORT_PREFIX}19700101_000000.json"
        old.write_text("{}", encoding="utf-8")
        os.utime(old, (_time.time() - 86400 * 400,) * 2)
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO knowledge_edges(edge_id, edge_key, relation, active, valid_to) "
                "VALUES('ED-old','k-old','purchased',0,datetime('now','localtime','-400 days'))")
        res = kg.knowledge_maintenance()
        self.assertFalse(old.exists(), "过期的知识包应被清理")
        self.assertGreaterEqual(res["edges_pruned"], 1)
        self.assertEqual(res["errors"], [])


if __name__ == "__main__":
    unittest.main()
