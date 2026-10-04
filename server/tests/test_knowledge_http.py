# -*- coding: utf-8 -*-
"""知识资产治理 + 跨域关系索引的接口契约测试（HTTP 层）

为什么单独一层：单元测试全绿但"用户点得到的那条路"不可用，是本项目踩过的坑
（见 README「真实缺陷恰好都藏在这一层」）。这里按**前端真正会调的那条路**打一遍接口，
并核对关键字段与非破坏性（重复登记不产生重复、退役资产不混进在用清单）。

运行：cd server && python -m pytest tests/test_knowledge_http.py -q
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from routers import registry  # noqa: E402

try:  # 知识治理层由 kb-assets 队友实现；尚未合入时本文件整体跳过，避免误报红
    import db_knowledge_schema  # noqa: E402
    import knowledge_assets as ka  # noqa: E402
    _READY = True
except Exception:  # noqa: BLE001
    db_knowledge_schema = None
    ka = None
    _READY = False

_SKIP = "知识资产/关系层尚未就绪"


def _fresh_db():
    """建一个独立临时库（项目惯例：每个测试自建库并清 schema 缓存）。"""
    tmp = tempfile.TemporaryDirectory()
    db.DB_PATH = Path(tmp.name) / "knowledge_http.db"
    db._schema_ready.clear()
    with db.get_conn() as conn:
        db_knowledge_schema.init_knowledge_tables(conn)
        try:
            import db_relations
            db_relations.init_relations_tables(conn)
        except Exception:  # noqa: BLE001 —— 关系层未合入时只少一层，不跳过全体
            pass
    return tmp


def _client():
    app = FastAPI()
    from routers import knowledge, knowledge_assets, relations
    app.include_router(knowledge.router)
    app.include_router(knowledge_assets.router)
    app.include_router(relations.router)
    return TestClient(app)


@unittest.skipUnless(_READY, _SKIP)
class TestKnowledgeHttp(unittest.TestCase):
    def setUp(self):
        self._tmp = _fresh_db()
        self.c = _client()

    def tearDown(self):
        self._tmp.cleanup()

    def _post(self, **payload):
        """登记一件资产（把重复的 POST 收敛成一行，让用例只表达差异）。"""
        return self.c.post("/api/knowledge/assets", json=payload)

    # ---------- 手工登记与查证：一条完整的"知识 → 依据"动线 ----------
    def test_register_then_trace_sources(self):
        r = self.c.post("/api/knowledge/assets", json={
            "kind": "fact", "subject": "snapshot:本月",
            "statement": "本月进货 8025 元", "evidence": ["[本月] 进货 8,025 元"],
            "source_kind": "snapshot", "source_ref": "ledger:shop_snapshot",
            "confidence": 0.7})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["created"])
        self.assertEqual(body["asset"]["state"], "active")
        asset_id = body["asset"]["asset_id"]

        r2 = self.c.get(f"/api/knowledge/assets/{asset_id}/sources")
        self.assertEqual(r2.status_code, 200)
        src = r2.json()
        self.assertEqual(src["source_ref"], "ledger:shop_snapshot")
        self.assertEqual(src["evidence"], ["[本月] 进货 8,025 元"])
        self.assertEqual(src["volatility"], "volatile")   # 由来源 snapshot 推断

    def test_repeat_register_is_idempotent(self):
        payload = {"kind": "fact", "subject": "s1", "statement": "熟客 3 位",
                   "evidence": ["[熟客] 共 3 位"], "source_kind": "customer"}
        first = self.c.post("/api/knowledge/assets", json=payload).json()
        second = self.c.post("/api/knowledge/assets", json=payload).json()
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        lst = self.c.get("/api/knowledge/assets?state=all").json()
        self.assertEqual(lst["count"], 1, "重复登记不得新增行")

    def test_content_change_supersedes_and_versions(self):
        base = {"kind": "fact", "subject": "s2", "evidence": ["[本月] 收 100 元"],
                "source_kind": "snapshot"}
        v1 = self.c.post("/api/knowledge/assets", json={**base, "statement": "本月收 100 元"}).json()
        v2 = self.c.post("/api/knowledge/assets", json={**base, "statement": "本月收 200 元"}).json()
        self.assertTrue(v2["changed"])
        self.assertEqual(v2["superseded"], v1["asset"]["asset_id"])
        self.assertEqual(v2["asset"]["version"], 2)
        old = self.c.get(f"/api/knowledge/assets/{v1['asset']['asset_id']}").json()
        self.assertEqual(old["state"], "superseded")
        self.assertEqual(old["superseded_by"], v2["asset"]["asset_id"])
        self.assertTrue(old["valid_to"])
        # 默认列表只给在用的知识，退役/被替代的不混进来
        active = self.c.get("/api/knowledge/assets").json()
        self.assertEqual([a["asset_id"] for a in active["items"]], [v2["asset"]["asset_id"]])

    def test_invalid_enum_rejected(self):
        r = self._post(kind="想当然", subject="x", statement="y")
        self.assertEqual(r.status_code, 422, "铁律5：有限取值非法必须在请求期被拒")

    def test_list_filters(self):
        self._post(kind="strategy", subject="g1", statement="先稳后快", source_kind="evolution")
        self._post(kind="fact", subject="f1", statement="今天收 500", source_kind="snapshot")
        by_kind = self.c.get("/api/knowledge/assets?kind=strategy").json()
        self.assertEqual(by_kind["count"], 1)
        self.assertEqual(by_kind["items"][0]["kind"], "strategy")
        stable = self.c.get("/api/knowledge/assets?volatility=stable").json()
        self.assertEqual(stable["count"], 1)

    def test_get_missing_asset_404(self):
        self.assertEqual(self.c.get("/api/knowledge/assets/AS-19700101-001").status_code, 404)

    def test_list_query_enums_are_validated(self):
        """铁律5：列表查询的枚举非法要 422，不能静默返回空集（看起来像"没有这类知识"）。"""
        for q in ("kind=想当然", "state=bogus", "volatility=fast"):
            self.assertEqual(self.c.get(f"/api/knowledge/assets?{q}").status_code, 422, q)
        self.assertEqual(self.c.get("/api/knowledge/assets?state=all").status_code, 200)

    # ---------- 总览 / 核验 / 导出 ----------
    def test_summary_shape(self):
        s = self.c.get("/api/knowledge/summary").json()
        for key in ("assets", "relations", "policy", "note"):
            self.assertIn(key, s)
        self.assertIn("drift_volatile", s["policy"])
        # 资产块给的是各口径的规模（total/by_state/...），前端"知识台账"直接吃这些数
        self.assertIn("total", s["assets"])
        self.assertIn("by_state", s["assets"])
        self.assertIn("stale", s["assets"])

    def test_summary_verify_flag_runs_runtime_check(self):
        self._post(kind="fact", subject="s3", statement="现金还能撑 2.5 个月",
                   evidence=["cash_runway_months=2.5"], source_kind="snapshot")
        s = self.c.get("/api/knowledge/summary?verify=true").json()
        self.assertIsNotNone(s.get("verification"), "verify=true 必须带回核验结果")

    def test_verify_single_asset_endpoint(self):
        res = self._post(kind="fact", subject="s4", statement="今天净 350",
                         evidence=["today_balance=350"], source_kind="snapshot").json()
        r = self.c.post("/api/knowledge/verify", json={"asset_id": res["asset"]["asset_id"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("checked"), 1)

    def test_verify_rejects_non_volatile(self):
        res = self._post(kind="strategy", subject="g9", statement="稳定策略",
                         source_kind="evolution").json()
        r = self.c.post("/api/knowledge/verify", json={"asset_id": res["asset"]["asset_id"]}).json()
        self.assertEqual(r.get("checked"), 0)
        self.assertIn("不需要运行期核验", r.get("error", ""))

    def test_bundle_export_roundtrip(self):
        self._post(kind="fact", subject="s5", statement="临期 2 种",
                   evidence=["[临期] 2 种快过期"], source_kind="snapshot")
        r = self.c.post("/api/knowledge/bundle")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertIn("ai_shopkeeper_knowledge_", body["name"])
        package = json.loads(body["content"])
        self.assertEqual(package["format"], "xirang.knowledge.bundle/v1")
        self.assertGreaterEqual(package["counts"]["assets"], 1)
        self.assertIn("manifest", package)

    def test_backfill_endpoint_ok_on_empty_db(self):
        r = self.c.post("/api/knowledge/backfill")
        self.assertEqual(r.status_code, 200)
        self.assertIn("registered", r.json())

    # ---------- 关系索引：降级也必须可用 ----------
    def test_relations_endpoints_degrade_gracefully(self):
        for path in ("/api/relations/summary", "/api/relations/graph",
                     "/api/relations/chain?entity_type=customer&entity_ref=1"):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 200, f"{path} 不应 500：{r.text}")
        r = self.c.post("/api/relations/merge")
        self.assertEqual(r.status_code, 200, r.text)

    # ---------- 能力看板 ----------
    def test_capability_report_has_knowledge_block(self):
        import ai_quality
        report = ai_quality.capability_report(7)
        self.assertIn("knowledge", report)
        self.assertIn("assets_total", report["knowledge"])

    # ---------- 注册表与契约 ----------
    def test_registry_registers_new_domains(self):
        names = [d["name"] for d in registry.BUSINESS_DOMAINS]
        self.assertIn("knowledge", names)
        self.assertIn("relations", names)
        routers = registry.get_routers()
        self.assertEqual([d["name"] for d in registry.get_enabled_domains()],
                         [r.tags[0] for r in routers])
        paths = [route.path for r in routers for route in r.routes]
        self.assertIn("/api/knowledge/summary", paths)
        self.assertIn("/api/relations/summary", paths)

    def test_schema_literals_match_asset_constants(self):
        """铁律5 回归：schemas 的 Literal 与 knowledge_assets 的常量元组必须一致。

        两个坑都踩过，注释留档：
        ① 不要用正则全文搜 `kind: Literal[...]` —— schemas 里发票的
           `kind: Literal["out","in"]` 会先被匹配到；
        ② `volatility` 声明成 `Literal[...] | None`，注解是 Union，
           要先 get_args 拆 Union 再取内层 Literal。
        """
        from typing import get_args, get_origin, Literal
        import schemas

        def _literal_values(annotation) -> tuple:
            for arg in get_args(annotation):
                if get_origin(arg) is Literal:
                    return tuple(get_args(arg))
            return tuple(x for x in get_args(annotation) if isinstance(x, str))

        pairs = (
            (schemas.KnowledgeAssetIn.model_fields["kind"].annotation, ka.ASSET_KINDS),
            (schemas.KnowledgeAssetIn.model_fields["volatility"].annotation, ka.VOLATILITIES),
        )
        for annotation, constants in pairs:
            literals = _literal_values(annotation)
            self.assertTrue(literals, "字段注解里应有 Literal 取值")
            self.assertEqual(sorted(literals), sorted(constants),
                             "schemas 的 Literal 与 knowledge_assets 常量已漂移")


if __name__ == "__main__":
    unittest.main()
