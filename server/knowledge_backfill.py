"""knowledge_backfill.py — 把进化层已有的知识回填登记为知识资产

为什么单独一个文件：`knowledge_assets.py` 已到 400 行硬上限（arch_check LINE_FAIL），
这里只做"搬家"——回填逻辑一字未改，`knowledge_assets.backfill` 仍原样可用（末尾 re-export）。

回填的意义：进化层的基因/经验是**存量知识**（跑过很多次才攒下来的），
如果不登记成资产，它们就永远在治理视野之外——查不到来源、没有版本、不能标记过期。
本模块只读现有表 + 调 `register_asset`，**不改进化层数据**；单个域失败只记 errors，
不让一个坏域挡住其余（回填是"补课"，不该因为一处读不到就整体放弃）。
"""
from __future__ import annotations

from knowledge_assets import register_asset

__all__ = ["backfill"]


def backfill(domains=None) -> dict:
    """把进化层的基因/经验登记为知识资产（只读现有表 + 注册，不改进化层数据）。"""
    domains = list(domains) if domains else ["copy", "store", "review"]
    registered, by_kind, errors = 0, {}, []
    try:
        import db_evolution
    except Exception as e:  # noqa: BLE001
        return {"registered": 0, "by_kind": {}, "errors": [f"进化层不可用：{e}"]}

    for domain in domains:
        try:
            genes = db_evolution.get_all_genes(domain) or []
        except Exception as e:  # noqa: BLE001
            errors.append(f"{domain} 基因读取失败：{e}")
            genes = []
        for gene in genes:
            try:
                gene_id = str(gene.get("gene_id") or "")
                statement = (str(gene.get("system_prompt_addon") or "").strip()
                             or "；".join(str(s) for s in (gene.get("strategy_steps") or []))
                             or gene_id)
                res = register_asset("strategy", gene_id, statement,
                                     evidence=gene.get("trigger_signals"),
                                     source_kind="evolution",
                                     confidence=gene.get("confidence"))
                if res.get("created"):
                    registered += 1
                    by_kind["strategy"] = by_kind.get("strategy", 0) + 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{domain}/{gene.get('gene_id')} 登记失败：{e}")

        try:
            learnings = db_evolution.get_learnings(domain, limit=50) or []
        except Exception as e:  # noqa: BLE001
            errors.append(f"{domain} 经验读取失败：{e}")
            learnings = []
        for item in learnings:
            try:
                subject = str(item.get("pattern_key") or item.get("id") or "")
                details = str(item.get("details") or "").strip()
                recurrence = item.get("recurrence_count")
                statement = f"{details or subject}（复现 {recurrence} 次）" \
                    if recurrence is not None else (details or subject)
                res = register_asset("experience", subject, statement,
                                     evidence=[details] if details else [],
                                     source_kind="evolution")
                if res.get("created"):
                    registered += 1
                    by_kind["experience"] = by_kind.get("experience", 0) + 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{domain}/{item.get('id')} 登记失败：{e}")

    return {"registered": registered, "by_kind": by_kind, "errors": errors}
