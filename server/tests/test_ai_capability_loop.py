# -*- coding: utf-8 -*-
"""AI 能力闭环测试：入口置信度、掌柜裁决、复盘反馈。"""

import json

import ai
import heartbeat
import team_domains


class TestParseConfidence:
    def test_ambiguous_parse_is_marked_for_check(self, monkeypatch):
        payload = {
            "customer": "王姐", "item": "买菜", "amount": 20,
            "trans_type": "expense", "category": "进货",
            "note": "", "tags": [], "confidence": 0.4,
            "ambiguity": "可能是帮顾客代买",
        }
        monkeypatch.setattr(ai, "ai_available", lambda: True)
        monkeypatch.setattr(ai, "chat", lambda *args, **kwargs: json.dumps(payload, ensure_ascii=False))

        result = ai.parse_transaction("王姐买菜20块")

        assert result["needs_check"] is True
        assert result["confidence"] == 0.4
        assert result["question"] == "可能是帮顾客代买"

    def test_confident_parse_does_not_ask_user(self, monkeypatch):
        payload = {
            "customer": "", "item": "肉包", "amount": 6,
            "trans_type": "income", "category": "主营业务收入",
            "note": "", "tags": [], "confidence": 0.95,
            "ambiguity": "",
        }
        monkeypatch.setattr(ai, "ai_available", lambda: True)
        monkeypatch.setattr(ai, "chat", lambda *args, **kwargs: json.dumps(payload, ensure_ascii=False))

        result = ai.parse_transaction("卖了肉包6块")

        assert result["needs_check"] is False
        assert result["confidence"] == 0.95
        assert result.get("question") is None


class TestJudgeQuality:
    def test_judge_clamps_confidence_and_rejects_unknown_roles(self, monkeypatch):
        payload = {
            "verdict": "采纳账房判断",
            "adopted": ["账房先生", "不存在的人"],
            "confidence": 9,
            "evidence": ["进货 8025 元", "", "没有进项票", "多余证据1", "多余证据2", "多余证据3"],
            "final": "今天先补进货发票。",
        }
        monkeypatch.setattr(ai, "chat", lambda *args, **kwargs: json.dumps(payload, ensure_ascii=False))

        result = team_domains._decide("今日复盘", [("账房先生", "进货 8025 元没有票")])

        assert result["adopted"] == ["账房先生"]
        assert result["confidence"] == 1.0
        assert len(result["evidence"]) == 5


class TestReviewFeedback:
    def test_feedback_is_saved_and_compiled_for_next_review(self, monkeypatch):
        import db

        history = []

        def fake_get(domain, key):
            if domain == "ledger" and key == "daily_review_feedback":
                return {"value": history}
            return None

        def fake_set(domain, key, value):
            history.append(value[-1])

        monkeypatch.setattr(db, "get_domain_context", fake_get)
        monkeypatch.setattr(db, "set_domain_context", fake_set)
        monkeypatch.setattr(heartbeat, "daily_review_text", lambda: "今天先补进货发票")

        saved = heartbeat.record_review_feedback(False, "建议太笼统")
        compiled = heartbeat.latest_review_feedback()

        assert saved["ok"] is True
        assert saved["saved"]["reason"] == "建议太笼统"
        assert "建议太笼统" in compiled


class TestDecisionEvidence:
    def test_review_decision_evidence_is_exposed(self, monkeypatch):
        import db
        import shop_snapshot
        import skill_cards

        stored = {}
        process = {
            "confidence": 0.86,
            "evidence": ["进货 8025 元", "没有进项票"],
            "adopted": ["税务管事"],
            "verdict": "票税风险最急",
        }

        monkeypatch.setattr(shop_snapshot, "build_snapshot", lambda: "[今日]收入500元")
        monkeypatch.setattr(team_domains, "generate_daily_review",
                            lambda *args, **kwargs: ("今天先补进货票。", process))
        monkeypatch.setattr(db, "get_domain_context", lambda domain, key: None)
        monkeypatch.setattr(db, "set_domain_context",
                            lambda domain, key, value: stored.update({key: value}))
        monkeypatch.setattr(skill_cards, "build_context", lambda: {})
        monkeypatch.setattr(skill_cards, "build_layered_review",
                            lambda context, review: {"layer1_summary": review})

        text = heartbeat.generate_daily_review()

        assert text == "今天先补进货票。"
        assert stored["daily_review_decision"]["confidence"] == 0.86
        assert stored["daily_review_layers"]["judge"]["evidence"] == [
            "进货 8025 元", "没有进项票"]
