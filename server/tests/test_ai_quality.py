# -*- coding: utf-8 -*-
"""AI 质量门禁与能力基线测试。"""

import ai_quality


class TestDecisionQuality:
    def test_good_decision_passes(self):
        quality = ai_quality.evaluate_decision(
            {"confidence": 0.9, "evidence": ["进货 8025 元"]},
            "今天先补进货发票，明天前完成。",
        )
        assert quality["passed"] is True
        assert quality["reasons"] == []
        assert quality["score"] >= 0.95

    def test_low_confidence_no_evidence_and_no_action_fails(self):
        quality = ai_quality.evaluate_decision(
            {"confidence": 0.2, "evidence": []}, "这个月经营情况一般。")
        assert quality["passed"] is False
        assert quality["reasons"] == ["confidence_low", "evidence_missing", "action_missing"]

    def test_repair_adds_evidence_and_action_but_not_fake_confidence(self):
        quality = ai_quality.evaluate_decision(
            {"confidence": 0.2, "evidence": []}, "这个月经营情况一般。")
        text, repaired = ai_quality.repair_decision(
            "这个月经营情况一般。", quality,
            fallback_evidence=["进货 8025 元，没有进项票"])
        assert "要不要" in text
        assert repaired["evidence"] == ["进货 8025 元，没有进项票"]
        assert repaired["passed"] is False
        assert repaired["reasons"] == ["confidence_low"]

    def test_snapshot_evidence_prefers_urgent_facts(self):
        snapshot = "[今日]收入500元\n[发票]本月进货8025元，没有进项票\n[熟客]共3人"
        evidence = ai_quality.evidence_from_snapshot(snapshot)
        assert evidence == ["[发票]本月进货8025元，没有进项票"]


class TestCapabilityReport:
    def test_report_exposes_five_loop_stages(self, monkeypatch):
        import ai
        import metrics
        from db import get_domain_context

        contexts = {
            "daily_review_decision": {
                "confidence": 0.8, "evidence": ["进货 8025 元"],
            },
            "daily_review_quality": {
                "confidence": 0.8, "evidence": ["进货 8025 元"],
                "has_action": True, "passed": True, "reasons": [],
            },
            "daily_review_feedback": [{"useful": True}, {"useful": False}],
            "daily_review_layers": {"skills": [{"id": "x", "name": "抓漏票"}]},
            "shop_snapshot": "[今日]收入500元",
        }

        monkeypatch.setattr(ai, "ai_available", lambda: True)
        monkeypatch.setattr(metrics, "summarize", lambda days: {
            "calls": 6, "success_rate": 1.0,
            "latency_ms": {"p95": 800}, "cost_est_yuan": 0.05})
        monkeypatch.setattr("db.get_domain_context",
                            lambda domain, key: {"value": contexts.get(key)})

        report = ai_quality.capability_report(7)

        assert report["status"] == "healthy"
        assert report["loop"] == {
            "perception": True, "memory": True, "decision": True,
            "action": True, "feedback": True,
        }
        assert report["feedback"]["useful_rate"] == 0.5
        assert report["skills"]["registered"] >= 1
        assert report["runtime"]["success_rate"] == 1.0

    def test_capability_api_exposes_report(self, monkeypatch):
        from routers import metrics

        monkeypatch.setattr(
            ai_quality, "capability_report",
            lambda days: {"status": "healthy", "mode": "ai", "loop": {}})
        report = metrics.ai_capability_metrics(7)

        assert report["status"] == "healthy"
        assert report["mode"] == "ai"
