# -*- coding: utf-8 -*-
"""skill_cards 单元测试：触发安全、分层输出、上下文构建"""

import skill_cards


class TestSafeEval:
    def test_basic_trigger(self):
        ctx = {"month_purchase": 8025, "invoice_rate": 0.0}
        assert skill_cards._safe_eval("month_purchase > 5000 and invoice_rate < 0.5", ctx)

    def test_false_trigger(self):
        ctx = {"month_purchase": 100, "invoice_rate": 1.0}
        assert not skill_cards._safe_eval("month_purchase > 5000", ctx)

    def test_rejects_code_execution(self):
        assert not skill_cards._safe_eval("__import__('os').system('echo bad')", {})

    def test_rejects_unknown_variable(self):
        assert not skill_cards._safe_eval("not_defined > 1", {})


class TestEvaluateTriggers:
    def test_high_severity_first(self):
        ctx = {k: 0 for k in skill_cards.CONTEXT_VARS}
        ctx.update({
            "month_purchase": 8025, "invoice_rate": 0.0,
            "cash_runway_months": 1.2, "expiring_count": 2,
            "month_balance": -500, "today_balance": 100,
        })
        hits = skill_cards.evaluate_triggers(ctx, limit=3)
        assert len(hits) == 3
        assert all(hit["severity"] == "high" for hit in hits)
        assert hits[0]["id"] == "detect_missing_invoices"
        assert "8025" in hits[0]["summary"]

    def test_no_trigger(self):
        ctx = {k: 0 for k in skill_cards.CONTEXT_VARS}
        ctx.update({"invoice_rate": 1.0, "cash_runway_months": 99.0})
        assert skill_cards.evaluate_triggers(ctx) == []


class TestBuildLayeredReview:
    def test_three_layers(self):
        ctx = {k: 0 for k in skill_cards.CONTEXT_VARS}
        ctx.update({"month_purchase": 8025, "invoice_rate": 0.0})
        result = skill_cards.build_layered_review(ctx, full_review="完整复盘")
        assert result["layer1_summary"]
        assert result["layer2_detail"]
        assert result["skills"]
        assert result["layer3_analysis"]["full_review"] == "完整复盘"


class TestBuildContext:
    def test_builds_from_db(self, monkeypatch):
        import db

        monkeypatch.setattr(db, "today_summary", lambda: {
            "income": 580, "expense": 230, "balance": 350, "cnt": 12})
        monkeypatch.setattr(db, "monthly_summary", lambda: {
            "income": 12000, "expense": 9000, "balance": 3000,
            "categories": [{"category": "进货", "total": 8025}]})
        monkeypatch.setattr(db, "list_customers", lambda: [
            {"name": "陈伯", "last_visit": "2026-09-01"}])
        monkeypatch.setattr(db, "stock_summary", lambda: {
            "low_stock": [{"name": "酱油"}], "expiring": []})
        monkeypatch.setattr(db, "invoice_summary", lambda month: {
            "by_kind": [{"kind": "in", "total": 0}]})
        monkeypatch.setattr(db, "list_store_profiles", lambda: [
            {"cash_on_hand": 6000}])

        ctx = skill_cards.build_context()
        assert ctx["today_balance"] == 350
        assert ctx["month_purchase"] == 8025
        assert ctx["invoice_rate"] == 0
        assert ctx["customer_count"] == 1
        assert ctx["low_stock_count"] == 1
