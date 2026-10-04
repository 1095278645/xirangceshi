# -*- coding: utf-8 -*-
"""知识资产治理层测试：枚举护栏 → 幂等/版本化 → 运行期核验 → 抽取与回填。

重点（对应任务验收清单）：
  - 铁律5：非法枚举一律 ValueError，不静默兜底；
  - 幂等不新增行、内容变化走版本链（旧行 superseded + 指向新行）；
  - check_volatility 的 ok/drift/unknown 三分（**unknown 不等于 ok**）；
  - verify_asset 写回 verified_at / verify_ok / drift_note；
  - extract_assets / backfill 在空输入/空库上都不许抛异常。

运行：cd server && python -m pytest tests/test_knowledge_assets.py -q
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import knowledge_assets as ka
import knowledge_extract as kx
import knowledge_verify as kv


def _asset(**kw):
    """构造一条最小资产 dict（不落库，供 check_volatility 直测）。"""
    base = {
        "asset_id": "AS-20260101-001", "kind": "fact", "subject": "today_balance",
        "statement": "今天流水 350 元", "volatility": "volatile",
        "evidence": ["today_balance=350"], "confidence": 0.6,
        "source_kind": "snapshot", "version": 1,
        "verified_at": "", "verify_ok": True,
    }
    base.update(kw)
    return base


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self._tmp.name) / "kb.db"
        db._schema_ready.clear()
        self.addCleanup(db._schema_ready.clear)
        db.init_db()

    def tearDown(self):
        self._tmp.cleanup()

    def _count_rows(self):
        with db.get_conn() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM knowledge_assets").fetchone()["c"]


class TestEnumsAndGuards(_Base):
    def test_invalid_enum_values_raise(self):
        """铁律5：有限取值的非法输入必须在入口被拒，不许静默兜底。"""
        cases = (
            ("kind", lambda: ka.register_asset("bogus", "s", "x")),
            ("state", lambda: ka.register_asset("fact", "s", "x", state="bogus")),
            ("volatility", lambda: ka.register_asset("fact", "s", "x", volatility="fast")),
        )
        for field, call in cases:
            with self.subTest(field=field), self.assertRaises(ValueError):
                call()

    def test_volatility_inference_is_also_validated(self):
        """按来源推断出来的挥发度同样必须落在枚举里（推断不是躲过校验的后门）。"""
        with mock.patch.dict(ka.VOLATILITY_BY_SOURCE, {"manual": "sometimes"}):
            with self.assertRaises(ValueError):
                ka.register_asset("fact", "s", "x", source_kind="manual")

    def test_source_kind_unknown_infers_volatile(self):
        """未知来源按契约兜底推断为 volatile（.get(..., "volatile")），不是报错。"""
        res = ka.register_asset("fact", "s_infer", "x", source_kind="unknown_kind")
        self.assertEqual(res["asset"]["volatility"], "volatile")

    def test_enum_constants_and_evidence_normalization(self):
        for name in ("ASSET_KINDS", "ASSET_STATES", "VOLATILITIES",
                     "SOURCE_KINDS", "RUNTIME_SOURCES", "DIRTY_STATES"):
            values = getattr(ka, name)
            self.assertTrue(values, f"{name} 不能为空")
            self.assertEqual(len(set(values)), len(values), f"{name} 不能有重复值")
        self.assertEqual(ka.to_evidence(None), [])
        self.assertEqual(ka.to_evidence("  a  "), ["a"])
        self.assertEqual(ka.to_evidence([{"value": "v"}, {"statement": "s"}]), ["v", "s"])
        self.assertEqual(len(ka.to_evidence([str(i) for i in range(9)])), 5)   # 最多 5 条
        self.assertEqual(len(ka.to_evidence(["x" * 300])[0]), 200)             # 单条截断 200


class TestRegisterVersioning(_Base):
    def test_first_register_creates_active_v1(self):
        res = ka.register_asset("fact", "today_balance", "今天流水 350 元",
                                evidence=["today_balance=350"], source_kind="snapshot")
        self.assertTrue(res["created"])
        self.assertTrue(res["changed"])
        self.assertIsNone(res["superseded"])
        asset = res["asset"]
        self.assertEqual(asset["version"], 1)
        self.assertEqual(asset["state"], "active")
        self.assertEqual(asset["kind"], "fact")
        self.assertTrue(asset["asset_id"].startswith("AS-"))
        self.assertTrue(asset["valid_from"])
        self.assertEqual(asset["confidence"], 0.6)          # 有证据的 active → 0.6

    def test_duplicate_content_does_not_insert(self):
        args = dict(evidence=["today_balance=350"], source_kind="snapshot")
        first = ka.register_asset("fact", "today_balance", "今天流水 350 元", **args)
        second = ka.register_asset("fact", "today_balance", "今天流水 350 元", **args)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertFalse(second["changed"])
        self.assertIsNone(second["superseded"])
        self.assertEqual(second["asset"]["asset_id"], first["asset"]["asset_id"])
        self.assertEqual(self._count_rows(), 1)             # 关键：没有新增行

    def test_content_change_supersedes_and_bumps_version(self):
        first = ka.register_asset("fact", "today_balance", "今天流水 350 元",
                                  evidence=["today_balance=350"], source_kind="snapshot")
        old_id = first["asset"]["asset_id"]
        second = ka.register_asset("fact", "today_balance", "今天流水 980 元",
                                   evidence=["today_balance=980"], source_kind="snapshot")
        self.assertTrue(second["created"])
        self.assertTrue(second["changed"])
        self.assertEqual(second["superseded"], old_id)

        old = ka.get_asset(old_id)
        self.assertEqual(old["state"], "superseded")
        self.assertTrue(old["valid_to"])
        self.assertEqual(old["superseded_by"], second["asset"]["asset_id"])
        self.assertEqual(second["asset"]["version"], 2)
        self.assertTrue(second["asset"]["valid_from"])
        self.assertEqual(self._count_rows(), 2)

    def test_confidence_clamped_and_draft_default(self):
        low = ka.register_asset("fact", "s_low", "x", confidence=-3)
        high = ka.register_asset("fact", "s_high", "y", confidence=9)
        draft = ka.register_asset("fact", "s_draft", "z", state="draft")
        self.assertEqual(low["asset"]["confidence"], 0.0)
        self.assertEqual(high["asset"]["confidence"], 1.0)
        self.assertEqual(draft["asset"]["confidence"], 0.4)   # 非 active → 默认 0.4


class TestQueries(_Base):
    def _seed(self):
        ka.register_asset("fact", "subj_a", "知识A", source_kind="snapshot")   # volatile
        ka.register_asset("experience", "subj_b", "知识B", source_kind="evolution")  # stable
        ka.register_asset("fact", "subj_c", "知识C", state="draft",
                          source_kind="manual")                                 # draft/stable

    def test_list_filters_and_stats_consistent(self):
        self._seed()
        # 过滤
        self.assertEqual({a["subject"] for a in ka.list_assets(kind="fact")},
                         {"subj_a", "subj_c"})
        self.assertEqual({a["subject"] for a in ka.list_assets(state="draft")}, {"subj_c"})
        self.assertEqual({a["subject"] for a in ka.list_assets(volatility="volatile")},
                         {"subj_a"})
        self.assertEqual([a["subject"] for a in ka.list_assets(subject="subj_b")], ["subj_b"])
        self.assertLessEqual(len(ka.list_assets(limit=9999)), 500)   # limit 上限 500
        # 统计口径自洽
        stats = ka.asset_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(sum(stats["by_state"].values()), stats["total"])
        self.assertEqual(sum(stats["by_kind"].values()), stats["total"])
        self.assertEqual(sum(stats["by_volatility"].values()), stats["total"])
        self.assertEqual(stats["by_state"].get("draft"), 1)
        self.assertLessEqual(len(stats["latest_active"]), 5)
        self.assertEqual(stats["runtime_sources"], list(ka.RUNTIME_SOURCES))


class TestCheckVolatility(_Base):
    def test_ok_within_tolerance(self):
        chk = ka.check_volatility(_asset(), {"today_balance": lambda: 360})
        self.assertEqual(chk["status"], "ok")
        self.assertEqual(chk["expected"], 350.0)
        self.assertEqual(chk["found"], 360.0)
        self.assertAlmostEqual(chk["drift_ratio"], 10 / 350, places=5)

    def test_drift_over_tolerance(self):
        chk = ka.check_volatility(_asset(), {"today_balance": lambda: 980})
        self.assertEqual(chk["status"], "drift")
        self.assertAlmostEqual(chk["drift_ratio"], 1.8, places=5)

    def test_missing_verifier_is_unknown_not_ok(self):
        chk = ka.check_volatility(_asset(), {})
        self.assertEqual(chk["status"], "unknown")
        self.assertEqual(chk["expected"], 350.0)
        self.assertIsNone(chk["found"])

    def test_zero_expected_does_not_divide_by_zero(self):
        """expected=0 的核验边界：0 vs 0 必须 ok（不是除零崩溃、也不是永远 drift）。"""
        asset = _asset(evidence=["today_balance=0"])
        self.assertEqual(ka.check_volatility(asset, {"today_balance": lambda: 0})["status"], "ok")
        self.assertEqual(ka.check_volatility(asset, {"today_balance": lambda: 100})["status"], "drift")

    def test_verifier_failure_is_unknown(self):
        def _boom():
            raise RuntimeError("db down")
        self.assertEqual(ka.check_volatility(_asset(), {"today_balance": _boom})["status"], "unknown")

    def test_stable_tolerance_is_zero(self):
        # 三层容忍度的取值本身也要锁住（阈值入 config，不许散落魔法数）
        self.assertEqual(ka.source_tolerance("stable"), 0.0)
        self.assertEqual(ka.source_tolerance("slow"), 0.05)
        self.assertEqual(ka.source_tolerance("volatile"), 0.20)
        self.assertEqual(ka.source_tolerance("unknown"), ka.source_tolerance("volatile"))
        chk = ka.check_volatility(_asset(volatility="stable"), {"today_balance": lambda: 360})
        self.assertEqual(chk["status"], "drift")           # stable：容忍度 0，任何差异都 drift
        same = ka.check_volatility(_asset(volatility="stable"), {"today_balance": lambda: 350})
        self.assertEqual(same["status"], "ok")

    def test_keyless_evidence_uses_subject(self):
        asset = _asset(subject="breakeven_daily", evidence=["日销 1320 元"])
        chk = ka.check_volatility(asset, {"breakeven_daily": lambda: 1320})
        self.assertEqual(chk["status"], "ok")
        self.assertEqual(chk["name"], "breakeven_daily")
        self.assertEqual(chk["expected"], 1320.0)

    def test_no_verifiable_evidence_is_unknown(self):
        asset = _asset(subject="", evidence=["今天生意还行"])
        chk = ka.check_volatility(asset, {"today_balance": lambda: 350})
        self.assertEqual(chk["status"], "unknown")
        self.assertEqual(chk["name"], "")

    def test_build_verifiers_reads_db_layer(self):
        with db.get_conn() as conn:
            conn.execute("INSERT INTO transactions(trans_type, amount, category) "
                         "VALUES('income', 120, '主营业务收入')")
        verifiers = ka.build_verifiers()
        self.assertIsInstance(verifiers, dict)
        self.assertTrue(all(callable(fn) for fn in verifiers.values()))
        self.assertIn("today_income", verifiers)
        self.assertAlmostEqual(verifiers["today_income"](), 120.0)
        # 没有进货成本 → 读不到就**不产生**这个 key（不许塞 0 冒充"数据就是 0"）
        self.assertNotIn("gross_margin", verifiers)

    def test_build_verifiers_drops_failed_sources(self):
        with mock.patch("db.today_summary", side_effect=RuntimeError("boom")), \
                mock.patch("db.monthly_summary", side_effect=RuntimeError("boom")):
            verifiers = ka.build_verifiers()
        self.assertNotIn("today_income", verifiers)
        self.assertNotIn("month_income", verifiers)
        self.assertNotIn("month_purchase", verifiers)


class TestVerifyAndExpose(_Base):
    def test_verify_asset_persists_drift(self):
        res = ka.register_asset("fact", "today_balance", "今天流水 350 元",
                                evidence=["today_balance=350"], source_kind="snapshot")
        asset_id = res["asset"]["asset_id"]
        with mock.patch.object(ka, "build_verifiers",
                               return_value={"today_balance": lambda: 980}):
            out = ka.verify_asset(persist=True)
        self.assertEqual(out["checked"], 1)
        self.assertEqual(out["drift"], 1)
        self.assertEqual(out["ok"], 0)
        self.assertEqual(out["items"][0]["asset_id"], asset_id)
        self.assertAlmostEqual(out["items"][0]["drift_ratio"], 1.8, places=5)

        saved = ka.get_asset(asset_id)
        self.assertTrue(saved["verified_at"])
        self.assertFalse(saved["verify_ok"])
        self.assertIn("980", saved["drift_note"])
        self.assertTrue(out["checked_at"])

        # persist=False：只算不写（供只读预览用），库里已落盘的核验痕迹不应被改动
        stamp = saved["verified_at"]
        with mock.patch.object(ka, "build_verifiers",
                               return_value={"today_balance": lambda: 350}):
            preview = ka.verify_asset(persist=False)
        self.assertEqual(preview["ok"], 1)
        self.assertEqual(ka.get_asset(asset_id)["verified_at"], stamp)

    def test_verify_asset_single_id_only_checks_that_one(self):
        first = ka.register_asset("fact", "today_balance", "今天流水 350 元",
                                  evidence=["today_balance=350"], source_kind="snapshot")
        ka.register_asset("fact", "month_balance", "本月结余 100 元",
                          evidence=["month_balance=100"], source_kind="snapshot")
        with mock.patch.object(ka, "build_verifiers",
                               return_value={"today_balance": lambda: 350,
                                             "month_balance": lambda: 100}):
            out = ka.verify_asset(first["asset"]["asset_id"], persist=False)
        self.assertEqual(out["checked"], 1)
        self.assertEqual(out["items"][0]["asset_id"], first["asset"]["asset_id"])

    def test_expose_active_marks_stale_and_polishes(self):
        rows = [{
            "asset_id": "AS-20260101-001", "kind": "decision", "subject": "daily_review",
            "statement": "毛利率 0.4，建议明天多进点货", "volatility": "volatile",
            "confidence": 0.6, "evidence": ["month_purchase=5000"],
            "source_kind": "snapshot", "version": 1, "verified_at": "", "verify_ok": False,
        }]
        exposed = ka.expose_active(rows)
        self.assertEqual(len(exposed), 1)
        self.assertTrue(exposed[0]["stale"])
        self.assertTrue(exposed[0]["statement"])                 # polish 后仍非空
        self.assertNotEqual(exposed[0]["statement"], rows[0]["statement"])  # 术语被翻译

        fresh = ka.expose_active([dict(rows[0], verify_ok=True)])
        self.assertFalse(fresh[0]["stale"])

        stable = ka.expose_active([dict(rows[0], volatility="stable", verify_ok=False)])
        self.assertFalse(stable[0]["stale"])                     # 稳定资产不因 verify 失败算"过期"


class TestExtractAndBackfill(_Base):
    def test_extract_assets_empty_input_never_raises(self):
        self.assertIsInstance(kx.extract_assets(), list)      # 空输入不抛异常，返回空列表

    def test_extract_assets_registers_fact_and_attention(self):
        snapshot = "[今日] 收款 1320 元\n[本月] 进货 5000 元\n普通一行没有标签"
        context = {"month_purchase": 8000, "invoice_rate": 0.1, "stale_customer_days": 20,
                   "customer_count": 5, "low_stock_count": 0, "expiring_count": 0,
                   "month_income": 20000, "month_expense": 12000, "month_balance": 8000,
                   "today_income": 1320, "today_expense": 300, "today_balance": 1020,
                   "cash_runway_months": 6, "breakeven_daily": 500}
        out = kx.extract_assets(snapshot, context=context,
                                review="今天生意还行，建议明天多备点货",
                                confidence=0.7, adopted=["老王"])
        kinds = [item["asset"]["kind"] for item in out]
        self.assertIn("fact", kinds)
        self.assertIn("attention", kinds)
        self.assertIn("decision", kinds)
        self.assertIn("strategy", kinds)

        facts = [a for a in ka.list_assets(kind="fact") if a["subject"].startswith("snapshot:")]
        self.assertEqual({a["subject"] for a in facts},
                         {"snapshot:今日", "snapshot:本月"})
        attention = ka.list_assets(kind="attention")
        self.assertTrue(attention)
        self.assertEqual(attention[0]["volatility"], "stable")
        self.assertEqual(attention[0]["source_kind"], "skill_card")

    def test_extract_records_verifiable_evidence(self):
        """事实类资产必须**可核验**：快照行是人话，但资产要带上 key=value 证据。

        没有这条，运行期核验对事实资产永远只能给 unknown ——
        等于"装了核验机制却用不上"（这是端到端跑真实库时发现的）。
        """
        snapshot = "[今日] 收 1320 元、支 300 元\n[本月] 收 20000 元"
        context = {"today_income": 1320, "today_expense": 300, "today_balance": 1020,
                   "month_income": 20000, "month_expense": 12000, "month_balance": 8000,
                   "customer_count": 5, "stale_customer_days": 3, "low_stock_count": 0,
                   "expiring_count": 0, "month_purchase": 8000, "invoice_rate": 0.1,
                   "cash_runway_months": 6, "breakeven_daily": 500}
        kx.extract_assets(snapshot, context=context, review="今天的账")

        today = ka.get_asset(next(a["asset_id"] for a in ka.list_assets(kind="fact")
                                  if a["subject"] == "snapshot:今日"))
        self.assertTrue(any(e.startswith("today_balance=") for e in today["evidence"]),
                        f"事实资产应带可核验的 key=value 证据：{today['evidence']}")
        # 上下文里没有的键不许凭空造（不许塞 0 冒充）
        self.assertFalse(any(e.startswith("invoice_in_month=") for e in today["evidence"]))

        # 真值一致 → ok；真值变了 → drift。两种都用假 verifiers 验，不碰真实数据层
        self.assertEqual(kv.check_volatility(today, {"today_balance": lambda: 1020})["status"], "ok")
        drifted = kv.check_volatility(today, {"today_balance": lambda: 99999})
        self.assertEqual(drifted["status"], "drift")

    def test_extract_assets_is_idempotent(self):
        snapshot = "[今日] 收款 1320 元"
        context = {"month_purchase": 0, "invoice_rate": 0, "stale_customer_days": 0,
                   "customer_count": 0, "low_stock_count": 0, "expiring_count": 0,
                   "cash_runway_months": 9, "breakeven_daily": 0, "today_balance": 0,
                   "month_balance": 0, "month_income": 0, "month_expense": 0,
                   "today_income": 0, "today_expense": 0}
        kx.extract_assets(snapshot, context=context, review="同一份复盘")
        before = self._count_rows()
        out = kx.extract_assets(snapshot, context=context, review="同一份复盘")
        self.assertEqual(self._count_rows(), before)     # 重复抽取不产生新版本
        self.assertTrue(all(not item["created"] for item in out))

    def test_backfill_empty_db(self):
        res = ka.backfill()
        self.assertEqual(res["registered"], 0)
        self.assertEqual(res["by_kind"], {})
        self.assertIsInstance(res["errors"], list)

    def test_backfill_registers_genes_and_learnings(self):
        import db_evolution
        db_evolution.save_gene("gene_kb_demo", "copy", ["场景=开业"],
                               system_prompt_addon="开业文案要先说优惠", confidence=0.8)
        db_evolution.record_learning("copy", "trigger", pattern_key="pat_kb",
                                     source="system", details="开头别用敬语")
        res = ka.backfill(["copy"])
        self.assertEqual(res["registered"], 2)
        self.assertEqual(res["by_kind"].get("strategy"), 1)
        self.assertEqual(res["by_kind"].get("experience"), 1)
        genes = [a for a in ka.list_assets(kind="strategy") if a["subject"] == "gene_kb_demo"]
        self.assertEqual(genes[0]["source_kind"], "evolution")
        self.assertEqual(genes[0]["volatility"], "stable")
        learnings = ka.list_assets(kind="experience")
        self.assertIn("复现", learnings[0]["statement"])
        self.assertIn("开头别用敬语", learnings[0]["statement"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
