"""knowledge_extract.py — 把一次掌柜复盘的产出物登记为知识资产

为什么单独一层：`knowledge_assets` 只管"资产怎么存、怎么验"，
这里管"复盘里哪些东西算知识"——每条知识都必须**带可核验的来源**，
不允许出现"无来源的断言"（对应参考图里 LLM Wiki / OKF+Git 的"有出处"）。

四段抽取（每段独立容错，单段失败只跳过该段并 warning，绝不让整次抽取崩掉）：
  A. decision  —— 复盘正文本身（来源：当日快照 + 复盘文本）
  B. attention —— 命中的技能卡片（每条卡片一件资产，来源：skill_card）
  C. fact      —— 快照里带 `[标签]` 的编号型事实（每条一行，来源：snapshot）
  D. strategy  —— 采纳归因（"复盘采纳了谁的判断"，来源：computed）

不调 AI、不联网：抽取是纯本地规则，无 Key 也能跑。
"""
from __future__ import annotations

import logging
import re

from knowledge_assets import register_asset, to_evidence

log = logging.getLogger("knowledge_extract")

__all__ = ["extract_assets"]

# 复盘正文入库时的截断长度（提示词预算之外的第二道护栏：资产不是日志，别整篇塞）
REVIEW_MAX_CHARS = 1200
LINE_MAX_CHARS = 200
_LABEL_RE = re.compile(r"\[([^\[\]]+)\]")

# 事实标签 → 上下文里可核验的键。
# 为什么需要：快照行是**人话**（"[今日] 收 580 元、支 230 元…"），
# 而运行期核验要的是 `key=value`（`today_balance=350`）。
# 不补这一层，事实类资产就永远只能验出 unknown —— 等于"有核验机制但用不上"。
# 只登记**上下文真有值**的键：拿不到就不写，绝不塞 0 冒充（0 是"没用数据"而非"没数据"）。
_CONTEXT_KEYS_BY_LABEL = {
    "今日": ("today_income", "today_expense", "today_balance"),
    "本月": ("month_income", "month_expense", "month_balance", "month_purchase"),
    "经营水平": ("gross_margin",),
    "熟客": ("customer_count", "stale_customer_days"),
    "库存": ("low_stock_count", "expiring_count"),
    "待补货": ("low_stock_count",),
    "临期": ("expiring_count",),
    "发票": ("invoice_in_month",),
}


def _verifiable_evidence(label: str, context) -> list[str]:
    """把上下文里的动态事实写成 `key=value`，让运行期核验真的能对账。"""
    if not isinstance(context, dict):
        return []
    keys = _CONTEXT_KEYS_BY_LABEL.get(label)
    if not keys:
        return []
    items = []
    for key in keys:
        value = context.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        # 0 在这个项目里是"拿不到数"的常见占位（build_context 对拿不到的维度就填 0）。
        # 把它写进证据，会让运行期核验把"没数据"误判成"数字变了"（实测：
        # breakeven_daily 被记成 0，真实保本线 860.2 → 假报警）。
        # 宁可不给这条证据（核验判 unknown，诚实），也不要给一条会误导的证据。
        if float(value) == 0.0:
            continue
        items.append(f"{key}={round(float(value), 2)}")
    return items


def _with_fact_context(context) -> dict:
    """补齐事实行需要、但 skill_cards 上下文里没有的键（采购额 / 进项票额）。

    为什么在这里补：快照的 `[本月]` 行会写"进货 X 元"，但 skill_cards 的上下文
    没有 `month_purchase`，导致这条事实登记不出可核验证据。补齐只用既有数据层函数，
    任一取数失败都跳过该键（**不塞 0**：0 是"没用数据"，不是"没数据"）。
    """
    ctx = dict(context) if isinstance(context, dict) else {}
    if "month_purchase" in ctx and "invoice_in_month" in ctx:
        return ctx
    try:
        from datetime import date
        from db import monthly_summary, invoice_summary
        if "month_purchase" not in ctx:
            for cat in (monthly_summary().get("categories") or []):
                if cat.get("category") == "进货":
                    ctx["month_purchase"] = float(cat.get("total") or 0)
                    break
        if "invoice_in_month" not in ctx:
            by = {x.get("kind"): x for x in
                  (invoice_summary(date.today().strftime("%Y-%m")).get("by_kind") or [])}
            if by.get("in") is not None:
                ctx["invoice_in_month"] = float((by.get("in") or {}).get("total") or 0)
    except Exception as e:  # noqa: BLE001 —— 补不上就不补，不许影响抽取
        log.warning("事实行上下文补齐失败（该行证据会少几项）：%s", e)
    return ctx


def extract_assets(snapshot="", *, context=None, review="", confidence=0.0,
                   evidence=None, adopted=None) -> list[dict]:
    """把复盘产出物登记为知识资产，返回 register_asset 的结果列表（含 created/changed）。"""
    out: list[dict] = []
    snapshot = snapshot or ""
    review = review or ""

    # A) 复盘正文 → decision 资产
    try:
        statement = (review or snapshot).strip()
        if statement:
            extracted = to_evidence(evidence)
            if not extracted:      # 没给证据就从快照抽（evidence or evidence_from_snapshot）
                try:
                    import ai_quality
                    extracted = to_evidence(ai_quality.evidence_from_snapshot(snapshot))
                except Exception as e:  # noqa: BLE001 —— 抽不到证据不是放弃登记的理由
                    log.warning("复盘证据抽取失败（仍登记正文）：%s", e)
            out.append(register_asset(
                "decision", "daily_review", statement[:REVIEW_MAX_CHARS],
                evidence=extracted, source_kind="snapshot",
                source_ref="ledger:daily_review", confidence=confidence))
    except Exception as e:  # noqa: BLE001
        log.warning("A 段（复盘正文）登记失败，已跳过：%s", e)

    # B) 命中的技能卡片 → attention 资产（判定规则稳定，故 volatility=stable）
    try:
        import skill_cards
        ctx = context if context is not None else skill_cards.build_context()
        for card in skill_cards.evaluate_triggers(ctx, limit=3) or []:
            card_id = str(card.get("id") or card.get("name") or "").strip()
            if not card_id:
                continue
            name = str(card.get("name") or card_id)
            summary = str(card.get("summary") or "")
            detail = str(card.get("detail") or "")
            out.append(register_asset(
                "attention", card_id, f"{name}：{summary}"[:REVIEW_MAX_CHARS],
                evidence=[summary, detail], source_kind="skill_card",
                volatility="stable", confidence=0.55))
    except Exception as e:  # noqa: BLE001
        log.warning("B 段（技能卡片）登记失败，已跳过：%s", e)

    # C) 快照里的 `[标签]` 行 → fact 资产（同一标签只登记一次）
    try:
        fact_ctx = _with_fact_context(context)
        seen: set[str] = set()
        for raw_line in snapshot.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = _LABEL_RE.search(line)
            if not match:
                continue
            label = match.group(1).strip()
            if not label or label in seen:
                continue
            seen.add(label)
            # 人话行 + 可核验的 key=value（后者让 volatile 资产的运行期核验有真值可比）
            out.append(register_asset(
                "fact", f"snapshot:{label}", line[:LINE_MAX_CHARS],
                evidence=[line] + _verifiable_evidence(label, fact_ctx),
                source_kind="snapshot"))
    except Exception as e:  # noqa: BLE001
        log.warning("C 段（编号型事实）登记失败，已跳过：%s", e)

    # D) 采纳归因 → strategy 资产
    try:
        for name in adopted or []:
            who = str(name or "").strip()
            if not who:
                continue
            out.append(register_asset(
                "strategy", f"adopted:{who}", f"复盘采纳了{who}的判断",
                source_kind="computed", volatility="slow", confidence=0.5))
    except Exception as e:  # noqa: BLE001
        log.warning("D 段（采纳归因）登记失败，已跳过：%s", e)

    return out
