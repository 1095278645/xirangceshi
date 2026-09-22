"""AI 掌柜质量门禁与能力基线。

把「输出一段 AI 建议」变成「可检验的经营判断」：
1. 决策必须有证据；
2. 输出必须有当天可执行动作；
3. 置信度过低时标记风险；
4. 缺证据时用技能卡片或原始快照修复，不凭空放行。
"""

from __future__ import annotations

from typing import Any

import plain_language


def evaluate_decision(
    decision: dict[str, Any] | None,
    output: str,
    evidence_candidates: list[str] | None = None,
) -> dict[str, Any]:
    """评估掌柜裁决质量，返回通过状态、分数和未满足条件。"""
    decision = decision or {}
    evidence = _clean_evidence(decision.get("evidence") or evidence_candidates or [])
    confidence = _clamp_confidence(decision.get("confidence", 0.0))
    has_action = plain_language.has_action(output)

    reasons = []
    if confidence < 0.55:
        reasons.append("confidence_low")
    if not evidence:
        reasons.append("evidence_missing")
    if not has_action:
        reasons.append("action_missing")

    score = round(
        confidence * 0.45
        + (0.30 if evidence else 0.0)
        + (0.25 if has_action else 0.0),
        2,
    )
    return {
        "passed": not reasons,
        "score": score,
        "confidence": confidence,
        "evidence": evidence,
        "has_action": has_action,
        "reasons": reasons,
    }


def repair_decision(
    output: str,
    quality: dict[str, Any],
    fallback_evidence: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """修复可自动补齐的质量问题；置信度不足只能标记，不能伪造。"""
    quality = dict(quality)
    if not quality.get("evidence"):
        quality["evidence"] = _clean_evidence(fallback_evidence or [])
    if not quality.get("has_action"):
        output = plain_language.so_what_check(output)
        quality["has_action"] = True

    reasons = []
    if quality.get("confidence", 0.0) < 0.55:
        reasons.append("confidence_low")
    if not quality.get("evidence"):
        reasons.append("evidence_missing")

    quality["reasons"] = reasons
    quality["passed"] = not reasons
    quality["repaired"] = True
    quality["score"] = round(
        quality.get("confidence", 0.0) * 0.45
        + (0.30 if quality.get("evidence") else 0.0)
        + (0.25 if quality.get("has_action") else 0.0),
        2,
    )
    return output, quality


def evidence_from_snapshot(snapshot: str, limit: int = 3) -> list[str]:
    """从原始快照抽取可核查事实，作为掌柜证据的兜底来源。"""
    high_risk = ("没建", "没设", "逾期", "临期", "见底", "快过期", "作废", "退货")
    lines = [line.strip() for line in (snapshot or "").splitlines() if line.strip()]
    urgent = [line for line in lines if any(word in line for word in high_risk)]
    if not urgent:
        business = [line for line in lines if any(word in line for word in ("进货", "发票"))]
        urgent = business
    if not urgent:
        urgent = [line for line in lines if any(char.isdigit() for char in line)]
    selected = urgent
    return selected[:max(0, limit)]


def capability_report(days: int = 7) -> dict[str, Any]:
    """汇总感知、记忆、决策、行动、反馈五段闭环的运行状态。"""
    import ai
    import metrics
    from db import get_domain_context

    decision = _context_value(get_domain_context("ledger", "daily_review_decision"))
    quality = _context_value(get_domain_context("ledger", "daily_review_quality"))
    feedback_history = _context_value(get_domain_context("ledger", "daily_review_feedback"))
    layers = _context_value(get_domain_context("ledger", "daily_review_layers"))
    snapshot = _context_value(get_domain_context("ledger", "shop_snapshot"))

    if not isinstance(feedback_history, list):
        feedback_history = []
    feedback_total = len(feedback_history)
    useful_count = sum(
        1 for item in feedback_history
        if isinstance(item, dict) and item.get("useful") is True
    )

    try:
        runtime = metrics.summarize(days)
    except Exception:
        runtime = {"calls": 0, "ok": 0, "failed": 0, "success_rate": None}

    skills = layers.get("skills", []) if isinstance(layers, dict) else []
    evidence = quality.get("evidence", []) if isinstance(quality, dict) else []
    has_decision = bool(decision) and bool(evidence)
    confidence = float(quality.get("confidence", 0.0)) if isinstance(quality, dict) else 0.0
    has_action = bool(quality.get("has_action")) if isinstance(quality, dict) else False

    if not ai.ai_available():
        status = "degraded"
    elif has_decision and has_action and confidence >= 0.55:
        status = "healthy"
    else:
        status = "warning"

    return {
        "status": status,
        "mode": "ai" if ai.ai_available() else "rule_fallback",
        "loop": {
            "perception": bool(snapshot),
            "memory": bool(decision or layers or feedback_history),
            "decision": has_decision,
            "action": has_action,
            "feedback": feedback_total > 0,
        },
        "decision": {
            "confidence": confidence,
            "evidence_count": len(evidence),
            "passed": bool(quality.get("passed")) if isinstance(quality, dict) else False,
            "reasons": quality.get("reasons", []) if isinstance(quality, dict) else [],
        },
        "feedback": {
            "total": feedback_total,
            "useful": useful_count,
            "not_useful": feedback_total - useful_count,
            "useful_rate": round(useful_count / feedback_total, 4) if feedback_total else None,
        },
        "skills": {
            "registered": _registered_skill_count(),
            "last_triggered": [
                {"id": item.get("id"), "name": item.get("name"), "severity": item.get("severity")}
                for item in skills if isinstance(item, dict)
            ][:3],
        },
        "runtime": {
            "window_days": days,
            "calls": runtime.get("calls", 0),
            "success_rate": runtime.get("success_rate"),
            "p95_latency_ms": (runtime.get("latency_ms") or {}).get("p95", 0),
            "cost_est_yuan": runtime.get("cost_est_yuan", 0.0),
        },
        "safeguards": {
            "input_confidence_check": True,
            "decision_evidence_required": True,
            "action_required": True,
            "user_feedback_loop": True,
            "rule_fallback": True,
        },
    }


def _context_value(item: dict | None) -> Any:
    return item.get("value") if isinstance(item, dict) else None


def _clean_evidence(values: list) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()][:5]


def _clamp_confidence(value) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.0
    return round(min(1.0, max(0.0, confidence)), 2)


def _registered_skill_count() -> int:
    try:
        import skill_cards
        return len(skill_cards.SKILL_CARDS)
    except Exception:
        return 0
